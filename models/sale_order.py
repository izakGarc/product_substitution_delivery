# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_product_available_qty(self, product, location_id):
        """Obtiene la cantidad disponible real del producto calculando: Cantidad a la mano - Saliente"""
        try:
            # Obtener cantidad a la mano (stock físico)
            qty_on_hand = product.with_context(location=location_id.id).qty_available
            
            # Obtener cantidad saliente (movimientos pendientes de salida)
            outgoing_qty = product.with_context(location=location_id.id).outgoing_qty
            
            # Calcular stock disponible libre = Cantidad a la mano - Saliente
            available_qty = qty_on_hand - outgoing_qty
            
            _logger.info(f"Producto {product.default_code or product.name} en ubicación {location_id.name}:")
            _logger.info(f"  - Cantidad a la mano: {qty_on_hand}")
            _logger.info(f"  - Cantidad saliente: {outgoing_qty}")
            _logger.info(f"  - Stock disponible libre: {available_qty}")
            
            return available_qty
            
        except Exception as e:
            _logger.error(f"Error calculando stock para {product.name}: {e}")
            return 0

    def _get_substitute_if_no_stock(self, product, location_id, required_qty):
            """Obtiene el producto sustituto si no hay stock suficiente para la cantidad requerida"""
            try:
                # Verificar si el producto tiene un sustituto configurado
                substitute = product.product_tmpl_id.delivery_substitute_id
                
                if not substitute:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustituto configurado")
                    return product
                
                # Calcular stock disponible
                available_qty = self._get_product_available_qty(product, location_id)
                
                # CAMBIO PRINCIPAL: Verificar si hay stock suficiente para la cantidad requerida
                if available_qty < required_qty:
                    _logger.info(f"*** STOCK INSUFICIENTE para {product.default_code or product.name} ***")
                    _logger.info(f"    Stock libre disponible: {available_qty}")
                    _logger.info(f"    Cantidad requerida: {required_qty}")
                    _logger.info(f"    Faltante: {required_qty - available_qty}")
                    _logger.info(f"    Usando sustituto: {substitute.default_code or substitute.name}")
                    return substitute
                else:
                    _logger.info(f"✓ Stock suficiente para {product.default_code or product.name}: {available_qty} unidades libres (requiere {required_qty})")
                    return product
                    
            except Exception as e:
                _logger.error(f"Error en _get_substitute_if_no_stock: {e}")
                return product

    def _get_line_warehouse_location(self, line):
            """Obtiene la ubicación de stock del almacén específico de la línea de venta"""
            try:
                # NUEVO: Verificar si la línea tiene un almacén específico configurado
                if hasattr(line, 'warehouse_id') and line.warehouse_id:
                    warehouse = line.warehouse_id
                    _logger.info(f"Línea {line.id} tiene almacén específico: {warehouse.name}")
                else:
                    # Si no hay almacén específico en la línea, usar el de la orden
                    warehouse = line.order_id.warehouse_id
                    _logger.info(f"Línea {line.id} usa almacén de la orden: {warehouse.name}")
                
                if not warehouse:
                    # Fallback: buscar almacén por defecto de la empresa
                    warehouse = self.env['stock.warehouse'].search([
                        ('company_id', '=', line.order_id.company_id.id)
                    ], limit=1)
                    _logger.warning(f"Usando almacén por defecto: {warehouse.name if warehouse else 'NINGUNO'}")
                
                if warehouse:
                    return warehouse.lot_stock_id
                else:
                    raise UserError("No se pudo determinar el almacén para la línea de venta")
                    
            except Exception as e:
                _logger.error(f"Error obteniendo ubicación de almacén: {e}")
                # Fallback seguro
                default_warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', line.order_id.company_id.id)
                ], limit=1)
                return default_warehouse.lot_stock_id if default_warehouse else None

    def _process_product_substitutions(self):
        """Procesar sustituciones de productos sin stock antes de confirmar"""
        _logger.info("=== Iniciando procesamiento de sustituciones ===")

        for order in self:
            # NUEVO: Verificar si es una orden de Producteca
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)
            
            if producteca_order:
                _logger.info(f"Orden de Producteca detectada: {producteca_order.name}")
            
            substituted_products = []  # Lista para trackear productos sustituidos
            lines_to_remove = []
            new_lines_to_create = []

            for line in order.order_line:
                if not line.product_id:
                    continue

                # Obtener la ubicación del almacén para esta línea
                location = self._get_line_warehouse_location(line)
                if not location:
                    _logger.error(f"No se pudo obtener ubicación para línea {line.id}")
                    continue

                _logger.info(f"Procesando línea {line.id} - Producto: {line.product_id.default_code} - Almacén: {location.name}")
                
                original_product = line.product_id
                original_qty = line.product_uom_qty
                original_price = line.price_unit

                # Determinar si se requiere sustitución
                substitute_product = self._get_substitute_if_no_stock(original_product, location, original_qty)

                # Si no hubo sustitución, continuar
                if substitute_product.id == original_product.id:
                    continue

                _logger.info(f"SUSTITUCIÓN DETECTADA: {original_product.default_code} -> {substitute_product.default_code}")

                # NUEVO: Registrar el producto sustituido para Producteca
                substituted_products.append(original_product.default_code or str(original_product.id))

                # Verificar si el sustituto tiene una BoM tipo phantom (kit)
                bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', substitute_product.product_tmpl_id.id),
                    ('type', '=', 'phantom')
                ], limit=1)

                if bom:
                    _logger.info(f"BOM Phantom encontrado para {substitute_product.default_code}")

                    total_original_line = original_price * original_qty
                    total_all_components_qty = sum(bl.product_qty * original_qty for bl in bom.bom_line_ids)

                    for bom_line in bom.bom_line_ids:
                        component = bom_line.product_id
                        component_qty = bom_line.product_qty * original_qty
                        component_unit_price = total_original_line / total_all_components_qty

                        new_line_vals = {
                            'order_id': order.id,
                            'product_id': component.id,
                            'product_uom_qty': component_qty,
                            'product_uom': component.uom_id.id,
                            'price_unit': component_unit_price,
                            'name': f"{component.name} (sustituto de {original_product.default_code})",
                            'sequence': line.sequence,
                        }

                        if hasattr(line, 'warehouse_id') and line.warehouse_id:
                            new_line_vals['warehouse_id'] = line.warehouse_id.id

                        new_lines_to_create.append(new_line_vals)

                        _logger.info(f"Componente: {component.default_code}")
                        _logger.info(f"  - Qty total: {component_qty}")
                        _logger.info(f"  - Precio unitario: ${component_unit_price:,.2f}")
                        _logger.info(f"  - Subtotal: ${component_qty * component_unit_price:,.2f}")

                    # Agregar mensaje al chatter
                    order.message_post(
                        body=f"<b>SKU {original_product.default_code}</b> sin stock en {location.name}. "
                            f"Sustituido por componentes del kit <b>{substitute_product.default_code}</b>:<br/>"
                            f"• {len(bom.bom_line_ids)} componentes agregados<br/>"
                            f"• Precio original mantenido: ${original_price:,.2f} x {original_qty} = ${original_price * original_qty:,.2f}",
                        message_type='comment'
                    )

                else:
                    # Sustitución directa
                    _logger.info(f"Sustitución directa sin kit")

                    new_line_vals = {
                        'order_id': order.id,
                        'product_id': substitute_product.id,
                        'product_uom_qty': original_qty,
                        'product_uom': substitute_product.uom_id.id,
                        'price_unit': original_price,
                        'name': f"{substitute_product.name} (sustituto de {original_product.default_code})",
                        'sequence': line.sequence,
                    }

                    if hasattr(line, 'warehouse_id') and line.warehouse_id:
                        new_line_vals['warehouse_id'] = line.warehouse_id.id

                    new_lines_to_create.append(new_line_vals)

                    # Agregar mensaje al chatter
                    order.message_post(
                        body=f"<b>SKU {original_product.default_code}</b> sin stock en {location.name}. "
                            f"Sustituido por <b>SKU {substitute_product.default_code}</b><br/>"
                            f"• Cantidad: {original_qty}<br/>"
                            f"• Precio unitario: ${original_price:,.2f}<br/>"
                            f"• Subtotal: ${original_price * original_qty:,.2f}",
                        message_type='comment'
                    )

                # Siempre eliminar la línea original si hubo sustitución
                lines_to_remove.append(line)

           # NUEVO: Marcar productos sustituidos en la orden de Producteca ANTES de eliminar líneas
            if producteca_order and substituted_products:
                try:
                    current_tags = producteca_order.tags or ''
                    
                    # Evitar duplicados
                    if 'SUBSTITUTED:' not in current_tags:
                        substitution_tag = f"SUBSTITUTED:{','.join(substituted_products)}"
                        new_tags = f"{current_tags};{substitution_tag}" if current_tags else substitution_tag
                        
                        producteca_order.write({'tags': new_tags})

                    _logger.info(f"✓ Productos marcados como sustituidos en Producteca: {substituted_products}")
                    
                    # Agregar mensaje en la orden de Odoo (no en Producteca porque no tiene message_post)
                    if order:
                        order.message_post(
                            body=f"🔄 <b>Sustituciones aplicadas en Producteca:</b><br/>"
                                f"• Productos sustituidos: {', '.join(substituted_products)}<br/>"
                                f"• Tags en Producteca: {new_tags}<br/>"
                                f"• Las notificaciones de sincronización ignorarán estos productos",
                            message_type='comment'
                        )
                    
                except Exception as e:
                    _logger.error(f"Error marcando sustituciones en Producteca: {e}")

            # Eliminar líneas originales
            for line in lines_to_remove:
                _logger.info(f"Eliminando línea original: {line.product_id.default_code}")
                line.unlink()

            # Crear nuevas líneas
            for vals in new_lines_to_create:
                new_line = self.env['sale.order.line'].create(vals)
                _logger.info(f"Línea creada: {new_line.product_id.default_code}")

        _logger.info("=== Sustituciones completadas ===")

    def action_confirm(self):
        _logger.info("=== Iniciando confirmación de orden con validación de stock ===")
        
        # Procesar sustituciones ANTES de confirmar
        self._process_product_substitutions()
        
        # Llamar al método padre para confirmar la orden
        return super(SaleOrder, self).action_confirm()


class SaleOrderLineProtection(models.Model):
    _inherit = 'sale.order.line'
    
    @api.model
    def create(self, vals):
        """Override para bloquear creación de líneas de productos sustituidos"""
        
        # Verificar si la orden tiene productos sustituidos
        if 'order_id' in vals and vals['order_id']:
            order = self.env['sale.order'].browse(vals['order_id'])
            
            # Buscar la orden de Producteca relacionada
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)
            
            if producteca_order and producteca_order.tags and 'SUBSTITUTED:' in producteca_order.tags:
                # Obtener productos sustituidos
                substituted_products = []
                for tag_part in producteca_order.tags.split(';'):
                    if tag_part.startswith('SUBSTITUTED:'):
                        products = tag_part.replace('SUBSTITUTED:', '').split(',')
                        substituted_products.extend(products)
                
                # Verificar si el producto de esta línea está sustituido
                line_product = None
                if 'product_id' in vals and vals['product_id']:
                    product = self.env['product.product'].browse(vals['product_id'])
                    line_product = product.default_code
                
                if line_product and line_product in substituted_products:
                    _logger.info(f"🛡️🛡️ CREACIÓN DE SALE.ORDER.LINE BLOQUEADA - SKU: {line_product} 🛡️🛡️")
                    _logger.info(f"🛡️🛡️ Orden: {order.name} - Productos sustituidos: {substituted_products} 🛡️🛡️")
                    
                    # Agregar mensaje al chatter
                    order.message_post(
                        body=f"🛡️ <b>Creación de línea bloqueada (sale.order.line)</b><br/>"
                            f"• SKU bloqueado: {line_product}<br/>"
                            f"• Razón: Producto sustituido<br/>"
                            f"• Sistema de protección nivel 2 activo",
                        message_type='comment'
                    )
                    
                    # No crear la línea - retornar un recordset vacío
                    return self.env['sale.order.line']
        
        return super().create(vals)