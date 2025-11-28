# -*- coding: utf-8 -*-

from odoo import models, api, fields
import logging

_logger = logging.getLogger(__name__)

class ProductecaAccountProtection(models.Model):
    _inherit = "producteca.account"

    def import_sale(self, sale, noti):
        """
        Override para interceptar import_sale y proteger órdenes con sustituciones
        """
        psoid = sale.get("id")
        
        if psoid:
            # Buscar si la orden ya existe
            existing_order = self.env["producteca.sale_order"].sudo().search([
                ('conn_id', '=', psoid),
                ("connection_account", "=", self.id)
            ], limit=1)
            
            # Verificar si tiene sustituciones
            if existing_order and existing_order.tags and 'SUBSTITUTED:' in existing_order.tags:
                _logger.info(f"🛡️ PROTECCIÓN PRODUCTECA ACTIVADA - Orden: {existing_order.name}")
                _logger.info(f"🛡️ Tags de sustitución encontrados: {existing_order.tags}")
                
                return self._handle_protected_order(sale, existing_order, noti)
        
        # Si no hay sustituciones, proceder normalmente
        return super().import_sale(sale, noti)
    
    def _handle_protected_order(self, sale, producteca_order, noti):
        """
        Maneja órdenes protegidas - solo actualiza campos seguros
        """
        _logger.info(f"🛡️ NO SE REPROCESARÁN LÍNEAS para orden: {producteca_order.name}")
        
        result = []
        
        try:
            # Solo actualizar campos seguros de estado
            safe_fields = {}
            safe_field_names = [
                'amount', 'shippingCost', 'financialCost', 'paidApproved',
                'paymentStatus', 'deliveryStatus', 'paymentFulfillmentStatus', 
                'deliveryFulfillmentStatus', 'isOpen', 'isCanceled', 'hasAnyShipments'
            ]
            
            for field_name in safe_field_names:
                if field_name in sale:
                    safe_fields[field_name] = sale[field_name]
            
            # Manejar fecha especialmente
            if 'date' in sale:
                try:
                    from odoo.addons.odoo_connector_api_producteca.models.versions import ml_datetime
                    safe_fields['date'] = ml_datetime(sale['date'])
                except:
                    safe_fields['date'] = sale['date']
            
            if safe_fields:
                producteca_order.write(safe_fields)
                _logger.info(f"🔄 Solo campos seguros actualizados: {list(safe_fields.keys())}")
            
            # Actualizar pagos (sin crear nuevas líneas)
            self._update_payments_only(sale, producteca_order)
            
            # Actualizar envíos (sin crear nuevas líneas)
            self._update_shipments_only(sale, producteca_order)
            
            # Ejecutar acciones de negocio si es necesario
            if producteca_order.sale_order:
                self._execute_safe_actions(producteca_order, sale)
            
            _logger.info(f"✅ Orden protegida procesada - NO se agregó SKU sustituido")
            
        except Exception as e:
            error_msg = f"Error procesando orden protegida {producteca_order.name}: {str(e)}"
            _logger.error(error_msg)
            result.append({"error": error_msg})
        
        return result
    
    def _update_payments_only(self, sale, producteca_order):
        """Actualizar pagos existentes sin crear nuevos"""
        if "payments" not in sale:
            return
            
        for payment_data in sale["payments"]:
            payment_id = f"{producteca_order.conn_id}_{payment_data.get('id')}"
            
            existing_payment = self.env["producteca.payment"].sudo().search([
                ('conn_id', '=', payment_id),
                ('order_id', '=', producteca_order.id),
                ("connection_account", "=", self.id)
            ], limit=1)
            
            if existing_payment:
                # Solo actualizar estado, no crear nuevos pagos
                payment_fields = {}
                if 'status' in payment_data:
                    payment_fields['status'] = payment_data['status']
                if 'amount' in payment_data:
                    payment_fields['amount'] = payment_data['amount']
                
                if payment_fields:
                    existing_payment.write(payment_fields)
                    _logger.info(f"💳 Pago actualizado (sin recrear líneas)")
    
    def _update_shipments_only(self, sale, producteca_order):
        """Actualizar envíos existentes sin crear nuevos"""
        if "shipments" not in sale:
            return
            
        for shipment_data in sale["shipments"]:
            shipment_id = f"{producteca_order.conn_id}_{shipment_data.get('id')}"
            
            existing_shipment = self.env["producteca.shipment"].sudo().search([
                ('conn_id', '=', shipment_id),
                ('order_id', '=', producteca_order.id),
                ("connection_account", "=", self.id)
            ], limit=1)
            
            if existing_shipment:
                # Solo actualizar estado, no crear nuevos envíos
                shipment_fields = {}
                if 'method' in shipment_data:
                    method = shipment_data['method']
                    if 'status' in method:
                        shipment_fields['method_status'] = method['status']
                    if 'trackingNumber' in method:
                        shipment_fields['method_trackingNumber'] = method['trackingNumber']
                
                if shipment_fields:
                    existing_shipment.write(shipment_fields)
                    _logger.info(f"📦 Envío actualizado (sin recrear líneas)")
    
    def _execute_safe_actions(self, producteca_order, sale_data):
        """Ejecutar solo acciones seguras sin tocar productos"""
        sale_order = producteca_order.sale_order
        
        if not sale_order:
            return
        
        # Actualizar estado de la orden Producteca
        if producteca_order.isCanceled:
            producteca_order.state = "cancelled"
        elif producteca_order.paymentStatus == 'Approved':
            producteca_order.state = "paid"
        elif producteca_order.paymentStatus == 'Refunded':
            producteca_order.state = "refunded"
        elif producteca_order.isOpen:
            producteca_order.state = "confirmed"
        else:
            producteca_order.state = "payment_required"
        
        # Solo ejecutar acciones que no afecten productos
        config = self.configuration
        if not config or not config.import_sales_action:
            return
        
        import_action = config.import_sales_action
        cond_paid = producteca_order.paymentStatus in ['Approved']
        cond_cancelled = producteca_order.isCanceled
        
        # Confirmar orden pagada (sin tocar productos)
        if "payed_confirm_order" in import_action and cond_paid:
            if sale_order.state in ['draft', 'sent']:
                try:
                    sale_order.action_confirm()
                    _logger.info(f"✅ Orden protegida confirmada: {sale_order.name}")
                except Exception as e:
                    _logger.error(f"Error confirmando orden protegida: {str(e)}")
        
        # Cancelar si es necesario
        if cond_cancelled and sale_order.state not in ['cancel']:
            try:
                sale_order.action_cancel()
                sale_order.producteca_update_forbidden = True
                _logger.info(f"🚫 Orden protegida cancelada: {sale_order.name}")
            except Exception as e:
                _logger.error(f"Error cancelando orden protegida: {str(e)}")


class ProductecaOrderLineProtection(models.Model):
    _inherit = "producteca.sale_order_line"

    @api.model
    def create(self, vals):
        """
        Bloquear creación de líneas de productos sustituidos en Producteca
        """
        if 'order_id' in vals and vals['order_id']:
            producteca_order = self.env['producteca.sale_order'].browse(vals['order_id'])
            
            if producteca_order.tags and 'SUBSTITUTED:' in producteca_order.tags:
                # Parsear tags para obtener TODOS los SKUs bloqueados
                blocked_skus = self._parse_blocked_skus(producteca_order.tags)
                
                # Verificar si el SKU de esta línea está bloqueado
                line_sku = vals.get('variation_sku', '')
                
                if line_sku and line_sku in blocked_skus:
                    _logger.info(f"🛡️🛡️ CREACIÓN DE LÍNEA PRODUCTECA BLOQUEADA - SKU: {line_sku} 🛡️🛡️")
                    _logger.info(f"🛡️🛡️ SKUs bloqueados: {blocked_skus} 🛡️🛡️")
                    
                    # Agregar mensaje al chatter de la orden de Odoo si existe
                    if producteca_order.sale_order:
                        producteca_order.sale_order.message_post(
                            body=f"🛡️ <b>Creación de línea Producteca bloqueada</b><br/>"
                                f"• SKU bloqueado: {line_sku}<br/>"
                                f"• Razón: Producto sustituido<br/>"
                                f"• Sistema de protección activo",
                            message_type='comment'
                        )
                    
                    # No crear la línea - retornar recordset vacío
                    return self.env['producteca.sale_order_line']
        
        return super().create(vals)
    
    def _parse_blocked_skus(self, tags):
        """
        Parsear tags para extraer TODOS los SKUs que deben ser bloqueados:
        - Productos originales sustituidos (de SUBSTITUTED:)
        - Productos usados en sustitución (de MIXED:)
        
        Formato esperado: "SUBSTITUTED:SKU-A,SKU-B;MIXED:SKU-A[Sub1(2),Sub2(1)];SKU-B[Sub3(4)]"
        """
        blocked = set()
        
        if not tags:
            return blocked
        
        for tag_part in tags.split(';'):
            tag_part = tag_part.strip()
            
            # Extraer productos sustituidos originales
            if tag_part.startswith('SUBSTITUTED:'):
                products = tag_part.replace('SUBSTITUTED:', '').split(',')
                blocked.update([p.strip() for p in products if p.strip()])
            
            # Extraer productos usados en mezcla
            elif tag_part.startswith('MIXED:'):
                # Formato: MIXED:SKU-A[Sub1(2),Sub2(1)];SKU-B[Sub3(4)]
                mixed_content = tag_part.replace('MIXED:', '')
                
                # NO bloquear el SKU original (SKU-A), solo los sustitutos
                # Extraer solo lo que está dentro de corchetes
                import re
                # Buscar patrones: [Sub1(2),Sub2(1)]
                matches = re.findall(r'\[(.*?)\]', mixed_content)
                
                for match in matches:
                    # match = "Sub1(2),Sub2(1)"
                    substitutes = match.split(',')
                    for sub in substitutes:
                        # sub = "Sub1(2)"
                        sku = sub.split('(')[0].strip()
                        if sku:
                            # NO agregar a blocked - los sustitutos SÍ pueden recibir actualizaciones
                            pass
        
        _logger.info(f"🔍 SKUs bloqueados parseados: {blocked}")
        return blocked