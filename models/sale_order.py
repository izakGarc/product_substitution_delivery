# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    
    def _get_order_channel(self):
        """Obtiene el primer canal de la orden de venta"""
        if self.channel_ids:
            first_channel = self.channel_ids[0]
            _logger.info(f"📢 Canal de la orden: {first_channel.name} (ID: {first_channel.id})")
            return first_channel
        else:
            _logger.warning(f"⚠️ Orden {self.name} no tiene canal asignado")
            return None

    def _get_product_available_qty(self, product, location_id):
        """Obtiene la cantidad disponible real del producto calculando: Cantidad a la mano - Saliente"""
        try:
            qty_on_hand = product.with_context(location=location_id.id).qty_available
            outgoing_qty = product.with_context(location=location_id.id).outgoing_qty
            available_qty = qty_on_hand - outgoing_qty
            
            _logger.info(f"Producto {product.default_code or product.name} en ubicación {location_id.name}:")
            _logger.info(f"  - Cantidad a la mano: {qty_on_hand}")
            _logger.info(f"  - Cantidad saliente: {outgoing_qty}")
            _logger.info(f"  - Stock disponible libre: {available_qty}")
            
            return available_qty
            
        except Exception as e:
            _logger.error(f"Error calculando stock para {product.name}: {e}")
            return 0

    def _get_product_available_qty_by_location(self, product, location_id):
        """
        Obtiene stock disponible usando la ubicación ESPECÍFICA de la línea sustituta
        (stock.quant), no el almacén general.
        Calcula: suma(quantity) - suma(reserved_quantity) en los quants de esa ubicación.
        """
        try:
            domain = [
                ('product_id', '=', product.id),
                ('location_id', 'child_of', location_id.id),
            ]
            quants = self.env['stock.quant'].search(domain)
            on_hand = sum(quants.mapped('quantity'))
            reserved = sum(quants.mapped('reserved_quantity'))
            available = on_hand - reserved

            _logger.info(
                f"  [quant] {product.default_code or product.name} "
                f"@ {location_id.name}: "
                f"mano={on_hand} | reservado={reserved} | libre={available}"
            )
            return available

        except Exception as e:
            _logger.error(f"Error calculando stock por ubicación para {product.name}: {e}")
            return 0

    def _get_substitute_if_no_stock(self, product, location_id, required_qty, order_channel=None):
        """
        Obtiene el primer producto sustituto con stock suficiente según prioridad.
        Usa la ubicación ESPECÍFICA de cada línea sustituta (no el almacén general).
        Si hay sustitutos configurados, IGNORA el stock del producto original.
        Si ningún sustituto cubre la cantidad total requerida, retorna el producto ORIGINAL.
        Filtra por canal de la orden.
        """
        try:
            substitute_lines = product.product_tmpl_id.delivery_substitute_line_ids.sorted('sequence')
            
            if order_channel:
                substitute_lines = substitute_lines.filtered(
                    lambda l: l.channel_id.id == order_channel.id or not l.channel_id
                )
                _logger.info(f"🔍 Filtrando sustitutos por canal: {order_channel.name} (incluye genéricos sin canal)")
                _logger.info(f"📋 Sustitutos encontrados: {len(substitute_lines)}")
            else:
                substitute_lines = substitute_lines.filtered(lambda l: not l.channel_id)
                _logger.warning(f"⚠️ Orden sin canal asignado - Solo se usarán sustitutos genéricos (sin canal)")
                _logger.info(f"📋 Sustitutos genéricos encontrados: {len(substitute_lines)}")
            
            if not substitute_lines:
                if order_channel:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos para canal {order_channel.name}")
                else:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos genéricos (sin canal)")
                
                available_qty = self._get_product_available_qty(product, location_id)
                
                if available_qty >= required_qty:
                    _logger.info(f"✓ Stock suficiente para {product.default_code or product.name}: {available_qty} libres (requiere {required_qty})")
                else:
                    _logger.warning(f"⚠️ Stock insuficiente para {product.default_code or product.name}: {available_qty} disponibles (requiere {required_qty})")
                
                return product
            
            _logger.info(f"🔄 Producto tiene sustitutos configurados - IGNORANDO stock del producto original")
            _logger.info(f"*** BUSCANDO SUSTITUTO para {product.default_code or product.name} ***")
            _logger.info(f"    Cantidad requerida: {required_qty}")
            
            for idx, line in enumerate(substitute_lines, 1):
                substitute = line.substitute_product_id

                # ── FIX: usar ubicación específica del sustituto ──────────────
                sub_location = line.location_id if line.location_id else location_id
                substitute_qty = self._get_product_available_qty_by_location(substitute, sub_location)
                # ─────────────────────────────────────────────────────────────

                canal_info = f"canal: {line.channel_id.name}" if line.channel_id else "genérico (sin canal)"
                _logger.info(f"  → Probando sustituto #{idx} (seq: {line.sequence}, {canal_info}): {substitute.default_code or substitute.name}")
                _logger.info(f"    Ubicación sustituto: {sub_location.name}")
                _logger.info(f"    Stock disponible libre: {substitute_qty}")
                
                if substitute_qty >= required_qty:
                    _logger.info(f"  ✓✓✓ SUSTITUTO SELECCIONADO: {substitute.default_code or substitute.name}")
                    _logger.info(f"      Prioridad (sequence): {line.sequence}")
                    _logger.info(f"      Canal: {line.channel_id.name if line.channel_id else 'genérico'}")
                    _logger.info(f"      Stock disponible: {substitute_qty} (requiere {required_qty})")
                    return substitute
                else:
                    _logger.info(f"  ✗ Stock insuficiente ({substitute_qty} < {required_qty}), probando siguiente...")
            
            # ── FIX: si ningún sustituto cubre la cantidad → producto original ──
            _logger.warning(
                f"⚠️ NINGÚN SUSTITUTO CON STOCK SUFICIENTE para {product.default_code} "
                f"(requiere {required_qty}) → se mantiene el producto ORIGINAL"
            )
            return product
            # ────────────────────────────────────────────────────────────────────
        
        except Exception as e:
            _logger.error(f"Error en _get_substitute_if_no_stock: {e}")
            return product

    def _get_substitutes_for_quantity(self, product, location_id, required_qty, order_channel=None):
        """
        Obtiene lista de sustitutos necesarios para completar la cantidad con mezcla.
        Usa la ubicación ESPECÍFICA de cada línea sustituta (no el almacén general).
        Si hay sustitutos configurados, IGNORA el stock del producto original.
        Filtra por canal de la orden.
        Retorna lista de tuplas: [(producto, cantidad), ...]
        """
        try:
            product_template = product.product_tmpl_id
            substitute_lines = product_template.delivery_substitute_line_ids.sorted('sequence')
            
            if order_channel:
                substitute_lines = substitute_lines.filtered(
                    lambda l: l.channel_id.id == order_channel.id or not l.channel_id
                )
                _logger.info(f"🔍 Filtrando sustitutos por canal: {order_channel.name} (incluye genéricos sin canal)")
                _logger.info(f"📋 Sustitutos encontrados: {len(substitute_lines)}")
            else:
                substitute_lines = substitute_lines.filtered(lambda l: not l.channel_id)
                _logger.warning(f"⚠️ Orden sin canal asignado - Solo se usarán sustitutos genéricos (sin canal)")
                _logger.info(f"📋 Sustitutos genéricos encontrados: {len(substitute_lines)}")
            
            if not substitute_lines:
                if order_channel:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos para canal {order_channel.name}")
                else:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos genéricos (sin canal)")
                
                available_qty = self._get_product_available_qty(product, location_id)
                
                if available_qty >= required_qty:
                    _logger.info(f"✓ Stock suficiente para {product.default_code or product.name}: {available_qty} libres (requiere {required_qty})")
                    return [(product, required_qty)]
                else:
                    _logger.warning(f"⚠️ Stock insuficiente para {product.default_code or product.name}: {available_qty} disponibles (requiere {required_qty})")
                    return [(product, required_qty)]
            
            _logger.info(f"🔄 Producto tiene sustitutos configurados - IGNORANDO stock del producto original")
            _logger.info(f"*** BUSCANDO SUSTITUTOS MIXTOS para {product.default_code or product.name} ***")
            _logger.info(f"    Cantidad requerida: {required_qty}")
            _logger.info(f"🔓 Mezcla PERMITIDA - Intentando completar {required_qty} unidades con múltiples sustitutos")
            
            result = []
            remaining_qty = required_qty
            
            for idx, line in enumerate(substitute_lines, 1):
                if remaining_qty <= 0:
                    break
                
                substitute = line.substitute_product_id

                # ── FIX: usar ubicación específica del sustituto ──────────────
                sub_location = line.location_id if line.location_id else location_id
                substitute_qty = self._get_product_available_qty_by_location(substitute, sub_location)
                # ─────────────────────────────────────────────────────────────

                canal_info = f"canal: {line.channel_id.name}" if line.channel_id else "genérico (sin canal)"
                _logger.info(f"  → Evaluando sustituto #{idx} (seq: {line.sequence}, {canal_info}): {substitute.default_code or substitute.name}")
                _logger.info(f"    Ubicación sustituto: {sub_location.name}")
                _logger.info(f"    Stock disponible libre: {substitute_qty}")
                _logger.info(f"    Faltan por completar: {remaining_qty}")
                
                if substitute_qty <= 0:
                    _logger.info(f"  ✗ Sin stock, saltando...")
                    continue
                
                qty_to_use = min(substitute_qty, remaining_qty)
                
                bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', substitute.product_tmpl_id.id),
                    ('type', '=', 'phantom')
                ], limit=1)
                
                if bom:
                    qty_to_use = int(qty_to_use)
                    _logger.info(f"  📦 Es un KIT - Usando {qty_to_use} unidades completas")
                
                if qty_to_use > 0:
                    result.append((substitute, qty_to_use))
                    remaining_qty -= qty_to_use
                    _logger.info(f"  ✓ Agregado: {substitute.default_code} x {qty_to_use}")
                    _logger.info(f"  📊 Progreso: {required_qty - remaining_qty}/{required_qty} completadas")
            
            if remaining_qty > 0:
                _logger.warning(f"⚠️ Faltan {remaining_qty} unidades después de usar todos los sustitutos")
                _logger.warning(f"⚠️ Agregando línea del producto original para completar")
                result.append((product, remaining_qty))
            
            if not result:
                _logger.warning(f"⚠️ NINGÚN sustituto tiene stock - Usando producto original con cantidad solicitada")
                result = [(product, required_qty)]
            
            _logger.info(f"✅ Resultado final: {len(result)} líneas para completar {required_qty} unidades")
            for prod, qty in result:
                _logger.info(f"   - {prod.default_code} x {qty}")
            
            return result
            
        except Exception as e:
            _logger.error(f"Error en _get_substitutes_for_quantity: {e}")
            return [(product, required_qty)]

    def _get_line_warehouse_location(self, line):
        """Obtiene la ubicación de stock del almacén específico de la línea de venta"""
        try:
            if hasattr(line, 'warehouse_id') and line.warehouse_id:
                warehouse = line.warehouse_id
                _logger.info(f"Línea {line.id} tiene almacén específico: {warehouse.name}")
            else:
                warehouse = line.order_id.warehouse_id
                _logger.info(f"Línea {line.id} usa almacén de la orden: {warehouse.name}")
            
            if not warehouse:
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
            default_warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', line.order_id.company_id.id)
            ], limit=1)
            return default_warehouse.lot_stock_id if default_warehouse else None

    def _process_product_substitutions(self):
        """Procesar sustituciones de productos sin stock antes de confirmar"""
        _logger.info("=== Iniciando procesamiento de sustituciones ===")

        for order in self:
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)
            
            if producteca_order:
                _logger.info(f"Orden de Producteca detectada: {producteca_order.name}")
            
            substituted_products = []
            mixed_substitutions = {}
            lines_to_remove = []
            new_lines_to_create = []
            
            order_channel = order._get_order_channel()

            for line in order.order_line:
                if not line.product_id:
                    continue

                location = self._get_line_warehouse_location(line)
                if not location:
                    _logger.error(f"No se pudo obtener ubicación para línea {line.id}")
                    continue

                _logger.info(f"Procesando línea {line.id} - Producto: {line.product_id.default_code} - Almacén: {location.name}")
                
                original_product = line.product_id
                original_qty = line.product_uom_qty
                original_price = line.price_unit
                original_sku = original_product.default_code or str(original_product.id)

                allow_mix = original_product.product_tmpl_id.allow_mix_substitutes
                
                if allow_mix:
                    _logger.info(f"🔓 Producto permite MEZCLA de sustitutos")
                    substitutes_list = self._get_substitutes_for_quantity(original_product, location, original_qty, order_channel)
                    
                    if len(substitutes_list) == 1 and substitutes_list[0][0].id == original_product.id:
                        continue
                else:
                    _logger.info(f"🔒 Producto NO permite mezcla - Modo sustitución simple")
                    substitute_product = self._get_substitute_if_no_stock(original_product, location, original_qty, order_channel)
                    
                    if substitute_product.id == original_product.id:
                        continue
                    
                    substitutes_list = [(substitute_product, original_qty)]

                _logger.info(f"🔄 SUSTITUCIÓN DETECTADA para {original_sku}")
                
                if original_sku not in substituted_products:
                    substituted_products.append(original_sku)
                
                if allow_mix:
                    mixed_substitutions[original_sku] = []
                
                for substitute_product, substitute_qty in substitutes_list:
                    substitute_sku = substitute_product.default_code or str(substitute_product.id)
                    
                    if allow_mix and substitute_product.id != original_product.id:
                        mixed_substitutions[original_sku].append((substitute_sku, substitute_qty))
                    
                    bom = self.env['mrp.bom'].search([
                        ('product_tmpl_id', '=', substitute_product.product_tmpl_id.id),
                        ('type', '=', 'phantom')
                    ], limit=1)

                    if bom:
                        _logger.info(f"📦 BOM Phantom encontrado para {substitute_sku} x {substitute_qty}")

                        total_for_this_portion = original_price * substitute_qty
                        total_all_components_qty = sum(bl.product_qty * substitute_qty for bl in bom.bom_line_ids)

                        for bom_line in bom.bom_line_ids:
                            component = bom_line.product_id
                            component_qty = bom_line.product_qty * substitute_qty
                            component_unit_price = total_for_this_portion / total_all_components_qty if total_all_components_qty > 0 else 0

                            new_line_vals = {
                                'order_id': order.id,
                                'product_id': component.id,
                                'product_uom_qty': component_qty,
                                'product_uom': component.uom_id.id,
                                'price_unit': component_unit_price,
                                'name': f"{component.name} (sustituto de {original_sku})" if original_sku != component.default_code else f"{component.name}",
                                'sequence': line.sequence,
                            }

                            if hasattr(line, 'warehouse_id') and line.warehouse_id:
                                new_line_vals['warehouse_id'] = line.warehouse_id.id

                            new_lines_to_create.append(new_line_vals)

                    else:
                        _logger.info(f"📦 Sustitución directa: {substitute_sku} x {substitute_qty}")

                        new_line_vals = {
                            'order_id': order.id,
                            'product_id': substitute_product.id,
                            'product_uom_qty': substitute_qty,
                            'product_uom': substitute_product.uom_id.id,
                            'price_unit': original_price,
                            'name': f"{substitute_product.name} (sustituto de {original_sku})" if original_sku != substitute_product.default_code else f"{substitute_product.name}",
                            'sequence': line.sequence,
                        }

                        if hasattr(line, 'warehouse_id') and line.warehouse_id:
                            new_line_vals['warehouse_id'] = line.warehouse_id.id

                        new_lines_to_create.append(new_line_vals)

                if len(substitutes_list) == 1:
                    sub_prod, sub_qty = substitutes_list[0]
                    
                    bom = self.env['mrp.bom'].search([
                        ('product_tmpl_id', '=', sub_prod.product_tmpl_id.id),
                        ('type', '=', 'phantom')
                    ], limit=1)
                    
                    if bom:
                        order.message_post(
                            body=f"<b>SKU {original_sku}</b> sin stock en {location.name}. "
                                f"Sustituido por componentes del kit <b>{sub_prod.default_code}</b>:<br/>"
                                f"• {len(bom.bom_line_ids)} componentes agregados<br/>"
                                f"• Precio original mantenido: ${original_price:,.2f} x {original_qty} = ${original_price * original_qty:,.2f}",
                            message_type='comment'
                        )
                    else:
                        order.message_post(
                            body=f"<b>SKU {original_sku}</b> sin stock en {location.name}. "
                                f"Sustituido por <b>SKU {sub_prod.default_code}</b>:<br/>"
                                f"• Cantidad: {sub_qty}<br/>"
                                f"• Precio unitario: ${original_price:,.2f}<br/>"
                                f"• Subtotal: ${original_price * sub_qty:,.2f}",
                            message_type='comment'
                        )
                else:
                    lines_detail = ""
                    for sub_prod, sub_qty in substitutes_list:
                        sub_sku = sub_prod.default_code or str(sub_prod.id)
                        stock_status = "(sin stock)" if sub_prod.id == original_product.id else ""
                        lines_detail += f"• SKU {sub_sku} x {sub_qty} ud = ${original_price * sub_qty:,.2f} {stock_status}<br/>"
                    
                    order.message_post(
                        body=f"<b>SKU {original_sku}</b> sin stock en {location.name}. "
                            f"Sustitución mixta aplicada:<br/>{lines_detail}",
                        message_type='comment'
                    )

                lines_to_remove.append(line)

            if producteca_order and substituted_products:
                try:
                    current_tags = producteca_order.tags or ''
                    
                    substitution_tag = f"SUBSTITUTED:{','.join(substituted_products)}"
                    
                    mixed_parts = []
                    for original_sku, substitutes in mixed_substitutions.items():
                        if substitutes:
                            subs_detail = ','.join([f"{sku}({qty})" for sku, qty in substitutes])
                            mixed_parts.append(f"{original_sku}[{subs_detail}]")
                    
                    if mixed_parts:
                        mixed_tag = f"MIXED:{';'.join(mixed_parts)}"
                        new_tags = f"{substitution_tag};{mixed_tag}"
                    else:
                        new_tags = substitution_tag
                    
                    if current_tags and 'SUBSTITUTED:' not in current_tags:
                        new_tags = f"{current_tags};{new_tags}"
                    elif not current_tags:
                        pass
                    else:
                        new_tags = current_tags
                    
                    producteca_order.write({'tags': new_tags})
                    _logger.info(f"✓ Tags actualizados en Producteca: {new_tags}")
                    
                    order.message_post(
                        body=f"🔄 <b>Sustituciones aplicadas en Producteca:</b><br/>"
                            f"• Productos sustituidos: {', '.join(substituted_products)}<br/>"
                            f"• Tags: {new_tags}<br/>"
                            f"• Sistema de protección activo",
                        message_type='comment'
                    )
                    
                except Exception as e:
                    _logger.error(f"Error marcando sustituciones en Producteca: {e}")

            for line in lines_to_remove:
                _logger.info(f"Eliminando línea original: {line.product_id.default_code}")
                line.unlink()

            for vals in new_lines_to_create:
                new_line = self.env['sale.order.line'].create(vals)
                _logger.info(f"Línea creada: {new_line.product_id.default_code}")

        _logger.info("=== Sustituciones completadas ===")

    def action_confirm(self):
        _logger.info("=== Iniciando confirmación de orden con validación de stock ===")
        self._process_product_substitutions()
        return super(SaleOrder, self).action_confirm()


class SaleOrderLineProtection(models.Model):
    _inherit = 'sale.order.line'
    
    @api.model
    def create(self, vals):
        """Override para bloquear creación de líneas de productos sustituidos"""
        
        if 'order_id' in vals and vals['order_id']:
            order = self.env['sale.order'].browse(vals['order_id'])
            
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)
            
            if producteca_order and producteca_order.tags and 'SUBSTITUTED:' in producteca_order.tags:
                substituted_products = self._parse_substituted_products(producteca_order.tags)
                
                line_product = None
                if 'product_id' in vals and vals['product_id']:
                    product = self.env['product.product'].browse(vals['product_id'])
                    line_product = product.default_code
                
                if line_product and line_product in substituted_products:
                    _logger.info(f"🛡️🛡️ CREACIÓN DE SALE.ORDER.LINE BLOQUEADA - SKU: {line_product} 🛡️🛡️")
                    _logger.info(f"🛡️🛡️ Orden: {order.name} - Productos sustituidos: {substituted_products} 🛡️🛡️")
                    
                    order.message_post(
                        body=f"🛡️ <b>Creación de línea bloqueada (sale.order.line)</b><br/>"
                            f"• SKU bloqueado: {line_product}<br/>"
                            f"• Razón: Producto sustituido<br/>"
                            f"• Sistema de protección nivel 2 activo",
                        message_type='comment'
                    )
                    
                    return self.env['sale.order.line']
        
        return super().create(vals)
    
    def _parse_substituted_products(self, tags):
        """Parsear tags para extraer productos sustituidos"""
        substituted = []
        if not tags:
            return substituted
        
        for tag_part in tags.split(';'):
            if tag_part.startswith('SUBSTITUTED:'):
                products = tag_part.replace('SUBSTITUTED:', '').split(',')
                substituted.extend([p.strip() for p in products if p.strip()])
        
        return substituted