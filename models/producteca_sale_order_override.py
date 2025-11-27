# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

# Log cuando se carga el módulo
_logger.info("🎯🎯🎯 INICIANDO CARGA DE PRODUCTECA OVERRIDE 🎯🎯🎯")

try:
    class ProductecaSaleOrderOverride(models.Model):
        _inherit = 'producteca.sale_order'
        
        _logger.info("🎯 PRODUCTECA.SALE_ORDER ENCONTRADO Y HEREDADO 🎯")
        
        def _has_substitutions(self):
            """Verificar si la orden tiene productos sustituidos"""
            return self.tags and 'SUBSTITUTED:' in self.tags
        
        def _get_substituted_products(self):
            """Obtener lista de productos sustituidos"""
            if not self._has_substitutions():
                return []
            
            substituted_products = []
            for tag_part in self.tags.split(';'):
                if tag_part.startswith('SUBSTITUTED:'):
                    products = tag_part.replace('SUBSTITUTED:', '').split(',')
                    substituted_products.extend(products)
            return substituted_products
        
        def _log_blocked_action(self, action_name, additional_info=""):
            """Log cuando se bloquea una acción"""
            substituted = self._get_substituted_products()
            _logger.info(f"🛡️ {action_name} BLOQUEADO - Orden: {getattr(self, 'name', 'SIN_NOMBRE')}")
            _logger.info(f"🛡️ Productos sustituidos: {substituted}")
            if additional_info:
                _logger.info(f"🛡️ Info adicional: {additional_info}")
        
        def update(self):
            """Override para evitar recrear líneas sustituidas"""
            _logger.info(f"🚀 UPDATE ejecutándose - Orden: {getattr(self, 'name', 'SIN_NOMBRE')}")
            _logger.info(f"🚀 Tags actuales: {getattr(self, 'tags', 'SIN_TAGS')}")
            
            if self._has_substitutions():
                self._log_blocked_action("UPDATE")
                return  # Bloquear completamente
            
            _logger.info(f"✅ UPDATE procediendo normalmente")
            return super().update()
        
        def write(self, vals):
            """Override del método write para interceptar cambios"""
            _logger.info(f"🔄 WRITE ejecutándose - Orden: {getattr(self, 'name', 'SIN_NOMBRE')}")
            _logger.info(f"🔄 Valores a escribir: {list(vals.keys()) if vals else 'NINGUNO'}")
            
            if self._has_substitutions():
                # Campos seguros que sí podemos actualizar
                allowed_fields = {
                    'state', 'invoice_status', 'tags', 'paymentStatus', 
                    'deliveryStatus', 'paymentFulfillmentStatus', 
                    'deliveryFulfillmentStatus', 'date', 'amount', 
                    'shippingCost', 'financialCost'
                }
                
                # Filtrar solo campos seguros
                safe_vals = {k: v for k, v in vals.items() if k in allowed_fields}
                blocked_vals = {k: v for k, v in vals.items() if k not in allowed_fields}
                
                if blocked_vals:
                    self._log_blocked_action("WRITE", f"Campos bloqueados: {list(blocked_vals.keys())}")
                
                if safe_vals:
                    _logger.info(f"🔄 WRITE permitiendo campos seguros: {list(safe_vals.keys())}")
                    return super().write(safe_vals)
                else:
                    _logger.info(f"🛡️ WRITE completamente bloqueado - no hay campos seguros")
                    return True  # Simular éxito pero no hacer nada
            
            return super().write(vals)
        
        def ocapi_refresh(self, data={}):
            """Override del método ocapi_refresh"""
            _logger.info(f"🔄 OCAPI_REFRESH ejecutándose - Orden: {getattr(self, 'name', 'SIN_NOMBRE')}")
            
            if self._has_substitutions():
                self._log_blocked_action("OCAPI_REFRESH")
                return  # Bloquear completamente
            
            _logger.info(f"✅ OCAPI_REFRESH procediendo normalmente")
            return super().ocapi_refresh(data)
        
        def ocapi_fetch(self, force_update=False):
            """Override del método ocapi_fetch"""
            _logger.info(f"🔄 OCAPI_FETCH ejecutándose - Orden: {getattr(self, 'name', 'SIN_NOMBRE')}")
            
            if self._has_substitutions():
                self._log_blocked_action("OCAPI_FETCH")
                return {}  # Retornar datos vacíos
            
            _logger.info(f"✅ OCAPI_FETCH procediendo normalmente")
            return super().ocapi_fetch(force_update)

    _logger.info("🎯 PRODUCTECA.SALE_ORDER OVERRIDE CREADO EXITOSAMENTE 🎯")

except Exception as e:
    _logger.error(f"🚨 ERROR HEREDANDO PRODUCTECA.SALE_ORDER: {e} 🚨")

try:
    class ProductecaSaleOrderLineOverride(models.Model):
        _inherit = 'producteca.sale_order_line'
        
        _logger.info("🎯 PRODUCTECA.SALE_ORDER_LINE ENCONTRADO Y HEREDADO 🎯")
        
        @api.model
        def create(self, vals):
            """Override para bloquear creación de líneas sustituidas"""
            sku = vals.get('variation_sku') or vals.get('product_code') or 'SIN_SKU'
            _logger.info(f"📝 CREANDO línea de Producteca - SKU: {sku}")
            
            # Verificar si es un producto que fue sustituido
            if 'order_id' in vals and vals['order_id']:
                order = self.env['producteca.sale_order'].browse(vals['order_id'])
                if order and order._has_substitutions():
                    substituted_products = order._get_substituted_products()
                    
                    if sku in substituted_products:
                        _logger.info(f"🛡️ CREACIÓN DE LÍNEA BLOQUEADA - SKU sustituido: {sku}")
                        _logger.info(f"🛡️ Orden: {order.name} tiene productos sustituidos: {substituted_products}")
                        
                        # Agregar mensaje al chatter de la orden de Odoo
                        if order.sale_order:
                            order.sale_order.message_post(
                                body=f"🛡️ <b>Creación de línea bloqueada</b><br/>"
                                    f"• SKU bloqueado: {sku}<br/>"
                                    f"• Razón: Producto sustituido<br/>"
                                    f"• Sistema de protección activo",
                                message_type='comment'
                            )
                        
                        # No crear la línea, simular éxito
                        return self.browse()
            
            return super().create(vals)
        
        def write(self, vals):
            """Override para bloquear modificaciones de líneas sustituidas"""
            _logger.info(f"📝 MODIFICANDO líneas de Producteca - IDs: {self.ids}")
            
            for line in self:
                if line.order_id and line.order_id._has_substitutions():
                    substituted_products = line.order_id._get_substituted_products()
                    line_sku = line.variation_sku or line.product_code
                    
                    if line_sku in substituted_products:
                        _logger.info(f"🛡️ MODIFICACIÓN DE LÍNEA BLOQUEADA - SKU: {line_sku}")
                        return True  # Simular éxito pero no hacer nada
            
            return super().write(vals)
        
        def unlink(self):
            """Override para permitir eliminación de líneas sustituidas"""
            # Permitir eliminación normal - no bloquear unlink
            return super().unlink()

    _logger.info("🎯 PRODUCTECA.SALE_ORDER_LINE OVERRIDE CREADO EXITOSAMENTE 🎯")

except Exception as e:
    _logger.error(f"🚨 ERROR HEREDANDO PRODUCTECA.SALE_ORDER_LINE: {e} 🚨")

try:
    class ProductecaNotificationOverride(models.Model):
        _inherit = 'producteca.notification'
        
        _logger.info("🎯 PRODUCTECA.NOTIFICATION ENCONTRADO Y HEREDADO 🎯")
        
        def process_notification(self):
            """Override para evitar procesar actualizaciones de líneas sustituidas"""
            _logger.info(f"📨 PROCESANDO NOTIFICACIÓN ID: {getattr(self, 'id', 'SIN_ID')}")
            
            # Intentar encontrar la orden relacionada
            producteca_order = None
            
            # Buscar por diferentes campos posibles
            for field_name in ['producteca_sale_order', 'sale_order', 'order_id']:
                if hasattr(self, field_name) and getattr(self, field_name):
                    producteca_order = getattr(self, field_name)
                    break
            
            # Buscar por resource si no encontramos por campo directo
            if not producteca_order and hasattr(self, 'resource') and self.resource:
                orders = self.env['producteca.sale_order'].search([('conn_id', '=', self.resource)])
                if orders:
                    producteca_order = orders[0]
            
            if producteca_order and producteca_order._has_substitutions():
                substituted_products = producteca_order._get_substituted_products()
                _logger.info(f"🛡️ NOTIFICACIÓN IGNORADA - Orden: {producteca_order.name}")
                _logger.info(f"🛡️ Productos sustituidos: {substituted_products}")
                
                # Agregar mensaje en la orden de Odoo
                if producteca_order.sale_order:
                    producteca_order.sale_order.message_post(
                        body=f"🛡️ <b>Notificación de Producteca ignorada</b><br/>"
                            f"• Notificación ID: {self.id}<br/>"
                            f"• Productos sustituidos: {', '.join(substituted_products)}<br/>"
                            f"• Sistema de protección activo",
                        message_type='comment'
                    )
                
                return  # No procesar la notificación
            
            _logger.info(f"📨 Procesando notificación normalmente")
            return super().process_notification()

    _logger.info("🎯 PRODUCTECA.NOTIFICATION OVERRIDE CREADO EXITOSAMENTE 🎯")

except Exception as e:
    _logger.error(f"🚨 ERROR HEREDANDO PRODUCTECA.NOTIFICATION: {e} 🚨")

_logger.info("🎯🎯🎯 FINALIZANDO CARGA DE PRODUCTECA OVERRIDE 🎯🎯🎯")