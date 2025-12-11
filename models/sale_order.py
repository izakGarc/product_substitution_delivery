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

    def _get_substitute_if_no_stock(self, product, location_id, required_qty, order_channel=None):
        """
        Obtiene el primer producto sustituto con stock suficiente según prioridad.
        Filtra por canal de la orden.
        """
        try:
            # Obtener lista de sustitutos ordenados por secuencia y filtrados por canal
            substitute_lines = product.product_tmpl_id.delivery_substitute_line_ids.sorted('sequence')
            
            # Filtrar por canal
            if order_channel:
                # Si hay canal en la orden, buscar sustitutos con ese canal O sin canal (genéricos)
                substitute_lines = substitute_lines.filtered(
                    lambda l: l.channel_id.id == order_channel.id or not l.channel_id
                )
                _logger.info(f"🔍 Filtrando sustitutos por canal: {order_channel.name} (incluye genéricos sin canal)")
                _logger.info(f"📋 Sustitutos encontrados: {len(substitute_lines)}")
            else:
                # Si NO hay canal en la orden, solo usar sustitutos genéricos (sin canal)
                substitute_lines = substitute_lines.filtered(lambda l: not l.channel_id)
                _logger.warning(f"⚠️ Orden sin canal asignado - Solo se usarán sustitutos genéricos (sin canal)")
                _logger.info(f"📋 Sustitutos genéricos encontrados: {len(substitute_lines)}")

            if not substitute_lines:
                if order_channel:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos para canal {order_channel.name}")
                else:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos genéricos (sin canal)")
                return product
            
            # Verificar stock del producto original
            available_qty = self._get_product_available_qty(product, location_id)
            
            if available_qty >= required_qty:
                _logger.info(f"✓ Stock suficiente para {product.default_code or product.name}: {available_qty} unidades libres (requiere {required_qty})")
                return product
            
            _logger.info(f"*** STOCK INSUFICIENTE para {product.default_code or product.name} ***")
            _logger.info(f"    Stock libre disponible: {available_qty}")
            _logger.info(f"    Cantidad requerida: {required_qty}")
            _logger.info(f"    Faltante: {required_qty - available_qty}")
            
            # Variable para guardar el primer sustituto CON ALGO de stock
            first_with_stock = None
            
            # Iterar sobre sustitutos en orden de prioridad (sequence)
            for idx, line in enumerate(substitute_lines, 1):
                substitute = line.substitute_product_id
                substitute_qty = self._get_product_available_qty(substitute, location_id)
                
                _logger.info(f"  → Probando sustituto #{idx} (seq: {line.sequence}, canal: {line.channel_id.name}): {substitute.default_code or substitute.name}")
                _logger.info(f"    Stock disponible: {substitute_qty}")
                
                # Guardar el primero que tenga ALGO de stock (aunque no sea suficiente)
                if first_with_stock is None and substitute_qty > 0:
                    first_with_stock = substitute
                    _logger.info(f"    💾 Guardado como 'primer con stock' (por si ninguno tiene suficiente)")
                
                if substitute_qty >= required_qty:
                    _logger.info(f"  ✓✓✓ SUSTITUTO SELECCIONADO: {substitute.default_code or substitute.name}")
                    _logger.info(f"      Prioridad (sequence): {line.sequence}")
                    _logger.info(f"      Canal: {line.channel_id.name}")
                    _logger.info(f"      Stock disponible: {substitute_qty} (requiere {required_qty})")
                    return substitute
                else:
                    _logger.info(f"  ✗ Stock insuficiente ({substitute_qty} < {required_qty}), probando siguiente...")
            
            # Si ningún sustituto tiene stock suficiente
            if first_with_stock:
                # Usar el PRIMERO que tenga ALGO de stock
                _logger.warning(f"⚠️ NINGÚN SUSTITUTO CON STOCK SUFICIENTE")
                _logger.warning(f"⚠️ Usando PRIMER sustituto CON STOCK: {first_with_stock.default_code}")
                return first_with_stock
            else:
                # Si TODOS tienen stock = 0, usar el producto original
                _logger.error(f"❌ NINGÚN SUSTITUTO TIENE STOCK DISPONIBLE")
                _logger.error(f"❌ Usando producto original: {product.default_code}")
                return product
        
        except Exception as e:
            _logger.error(f"Error en _get_substitute_if_no_stock: {e}")
            return product

    def _get_substitutes_for_quantity(self, product, location_id, required_qty, order_channel=None):
        """
        Obtiene lista de sustitutos necesarios para completar la cantidad requerida con mezcla.
        PRIMERO usa el stock disponible del producto original.
        Filtra por canal de la orden.
        Retorna lista de tuplas: [(producto, cantidad), ...]
        """
        try:
            product_template = product.product_tmpl_id
            substitute_lines = product_template.delivery_substitute_line_ids.sorted('sequence')
            
            # Filtrar por canal
            if order_channel:
                # Si hay canal en la orden, buscar sustitutos con ese canal O sin canal (genéricos)
                substitute_lines = substitute_lines.filtered(
                    lambda l: l.channel_id.id == order_channel.id or not l.channel_id
                )
                _logger.info(f"🔍 Filtrando sustitutos por canal: {order_channel.name} (incluye genéricos sin canal)")
                _logger.info(f"📋 Sustitutos encontrados: {len(substitute_lines)}")
            else:
                # Si NO hay canal en la orden, solo usar sustitutos genéricos (sin canal)
                substitute_lines = substitute_lines.filtered(lambda l: not l.channel_id)
                _logger.warning(f"⚠️ Orden sin canal asignado - Solo se usarán sustitutos genéricos (sin canal)")
                _logger.info(f"📋 Sustitutos genéricos encontrados: {len(substitute_lines)}")

            if not substitute_lines:
                if order_channel:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos para canal {order_channel.name}")
                else:
                    _logger.info(f"Producto {product.default_code or product.name} no tiene sustitutos genéricos (sin canal)")
                return [(product, required_qty)]
            
            # Verificar stock del producto original
            available_qty = self._get_product_available_qty(product, location_id)
            
            if available_qty >= required_qty:
                _logger.info(f"✓ Stock suficiente para {product.default_code or product.name}: {available_qty} unidades libres (requiere {required_qty})")
                return [(product, required_qty)]
            
            _logger.info(f"*** STOCK INSUFICIENTE para {product.default_code or product.name} ***")
            _logger.info(f"    Stock libre disponible: {available_qty}")
            _logger.info(f"    Cantidad requerida: {required_qty}")
            _logger.info(f"    Faltante: {required_qty - available_qty}")
            _logger.info(f"🔓 Mezcla PERMITIDA - Intentando completar {required_qty} unidades con múltiples productos")
            
            result = []
            remaining_qty = required_qty
            
            # PRIMERO usar el stock del producto original si tiene algo
            if available_qty > 0:
                result.append((product, available_qty))
                remaining_qty -= available_qty
                _logger.info(f"  ✓ Usando stock del producto ORIGINAL: {product.default_code} x {available_qty}")
                _logger.info(f"  📊 Progreso: {available_qty}/{required_qty} completadas")
                _logger.info(f"  📊 Faltan: {remaining_qty}")
            
            # Ahora continuar con los sustitutos
            for idx, line in enumerate(substitute_lines, 1):
                if remaining_qty <= 0:
                    break
                
                substitute = line.substitute_product_id
                substitute_qty = self._get_product_available_qty(substitute, location_id)
                
                _logger.info(f"  → Evaluando sustituto #{idx} (seq: {line.sequence}, canal: {line.channel_id.name}): {substitute.default_code or substitute.name}")
                _logger.info(f"    Stock disponible: {substitute_qty}")
                _logger.info(f"    Faltan por completar: {remaining_qty}")
                
                if substitute_qty <= 0:
                    _logger.info(f"  ✗ Sin stock, saltando...")
                    continue
                
                # Tomar lo que haya disponible (hasta lo que falta)
                qty_to_use = min(substitute_qty, remaining_qty)
                
                # Verificar si es un kit (BOM Phantom)
                bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', substitute.product_tmpl_id.id),
                    ('type', '=', 'phantom')
                ], limit=1)
                
                if bom:
                    # Para kits, usar unidades completas (redondear hacia abajo)
                    qty_to_use = int(qty_to_use)
                    _logger.info(f"  📦 Es un KIT - Usando {qty_to_use} unidades completas")
                
                if qty_to_use > 0:
                    result.append((substitute, qty_to_use))
                    remaining_qty -= qty_to_use
                    _logger.info(f"  ✓ Agregado: {substitute.default_code} x {qty_to_use}")
                    _logger.info(f"  📊 Progreso: {required_qty - remaining_qty}/{required_qty} completadas")
            
            # Si aún falta cantidad después de usar todo, agregar línea sin stock
            if remaining_qty > 0:
                _logger.warning(f"⚠️ Faltan {remaining_qty} unidades - Agregando línea sin stock del producto original")
                result.append((product, remaining_qty))
            
            # Si no se pudo agregar nada (todos sin stock), usar producto original
            if not result:
                _logger.warning(f"⚠️ NINGÚN producto tiene stock - Usando producto original con cantidad solicitada")
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
            # Verificar si la línea tiene un almacén específico configurado
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
            # Verificar si es una orden de Producteca
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)
            
            if producteca_order:
                _logger.info(f"Orden de Producteca detectada: {producteca_order.name}")
            
            substituted_products = []  # SKUs originales sustituidos
            mixed_substitutions = {}  # {SKU_original: [(SKU_sustituto, cantidad), ...]}
            lines_to_remove = []
            new_lines_to_create = []
            
            # Obtener el canal de la orden (primer canal si hay múltiples)
            order_channel = order._get_order_channel()

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
                original_sku = original_product.default_code or str(original_product.id)

                # Verificar si permite mezcla
                allow_mix = original_product.product_tmpl_id.allow_mix_substitutes
                
                if allow_mix:
                    # MODO MEZCLA: Obtener lista de sustitutos (puede ser múltiple)
                    _logger.info(f"🔓 Producto permite MEZCLA de sustitutos")
                    substitutes_list = self._get_substitutes_for_quantity(original_product, location, original_qty, order_channel)
                    
                    # Si solo hay 1 elemento y es el producto original, no hay sustitución
                    if len(substitutes_list) == 1 and substitutes_list[0][0].id == original_product.id:
                        continue
                else:
                    # MODO SIMPLE: Obtener UN sustituto
                    _logger.info(f"🔒 Producto NO permite mezcla - Modo sustitución simple")
                    substitute_product = self._get_substitute_if_no_stock(original_product, location, original_qty, order_channel)
                    
                    # Si no hubo sustitución, continuar
                    if substitute_product.id == original_product.id:
                        continue
                    
                    # Convertir a formato lista para procesamiento uniforme
                    substitutes_list = [(substitute_product, original_qty)]

                _logger.info(f"🔄 SUSTITUCIÓN DETECTADA para {original_sku}")
                
                # Registrar el producto original como sustituido
                if original_sku not in substituted_products:
                    substituted_products.append(original_sku)
                
                # Registrar detalles de la mezcla (solo si hay más de 1 sustituto)
                if allow_mix:
                    mixed_substitutions[original_sku] = []
                
                # Procesar cada sustituto en la lista
                for substitute_product, substitute_qty in substitutes_list:
                    substitute_sku = substitute_product.default_code or str(substitute_product.id)
                    
                    # Registrar en mezcla (solo si no es el producto original y permite mezcla)
                    if allow_mix and substitute_product.id != original_product.id:
                        mixed_substitutions[original_sku].append((substitute_sku, substitute_qty))
                    
                    # Verificar si el sustituto tiene una BoM tipo phantom (kit)
                    bom = self.env['mrp.bom'].search([
                        ('product_tmpl_id', '=', substitute_product.product_tmpl_id.id),
                        ('type', '=', 'phantom')
                    ], limit=1)

                    if bom:
                        _logger.info(f"📦 BOM Phantom encontrado para {substitute_sku} x {substitute_qty}")

                        # Calcular precio total para esta porción
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
                                'name': f"{component.name} (sustituto de {original_sku})"  if original_sku != component.default_code else f"{component.name}",
                                'sequence': line.sequence,
                            }

                            if hasattr(line, 'warehouse_id') and line.warehouse_id:
                                new_line_vals['warehouse_id'] = line.warehouse_id.id

                            new_lines_to_create.append(new_line_vals)

                            _logger.info(f"  Componente: {component.default_code}")
                            _logger.info(f"    - Qty: {component_qty}")
                            _logger.info(f"    - Precio unitario: ${component_unit_price:,.2f}")

                    else:
                        # Sustitución directa (o porción de mezcla)
                        _logger.info(f"📦 Sustitución directa: {substitute_sku} x {substitute_qty}")

                        new_line_vals = {
                            'order_id': order.id,
                            'product_id': substitute_product.id,
                            'product_uom_qty': substitute_qty,
                            'product_uom': substitute_product.uom_id.id,
                            'price_unit': original_price,  # Mantener precio original
                            'name': f"{substitute_product.name} (sustituto de {original_sku})" if original_sku != substitute_product.default_code else f"{substitute_product.name}",
                            'sequence': line.sequence,
                        }

                        if hasattr(line, 'warehouse_id') and line.warehouse_id:
                            new_line_vals['warehouse_id'] = line.warehouse_id.id

                        new_lines_to_create.append(new_line_vals)

                # Agregar mensaje al chatter
                if len(substitutes_list) == 1:
                    # Sustitución simple
                    sub_prod, sub_qty = substitutes_list[0]
                    
                    # Verificar si es kit
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
                    # Sustitución mixta
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

                # Eliminar la línea original
                lines_to_remove.append(line)

            # Marcar productos sustituidos en la orden de Producteca CON DETALLE DE MEZCLA
            if producteca_order and substituted_products:
                try:
                    current_tags = producteca_order.tags or ''
                    
                    # Crear tag de productos sustituidos
                    substitution_tag = f"SUBSTITUTED:{','.join(substituted_products)}"
                    
                    # Crear tag de mezcla si aplica
                    mixed_parts = []
                    for original_sku, substitutes in mixed_substitutions.items():
                        if substitutes:  # Solo si hay sustitutos (mezcla activa)
                            # Formato: SKU_original[Sust1(qty),Sust2(qty)]
                            subs_detail = ','.join([f"{sku}({qty})" for sku, qty in substitutes])
                            mixed_parts.append(f"{original_sku}[{subs_detail}]")
                    
                    if mixed_parts:
                        mixed_tag = f"MIXED:{';'.join(mixed_parts)}"
                        new_tags = f"{substitution_tag};{mixed_tag}"
                    else:
                        new_tags = substitution_tag
                    
                    # Agregar a tags existentes
                    if current_tags and 'SUBSTITUTED:' not in current_tags:
                        new_tags = f"{current_tags};{new_tags}"
                    elif not current_tags:
                        pass  # new_tags ya está bien
                    else:
                        # Ya existe SUBSTITUTED, no duplicar
                        new_tags = current_tags
                    
                    producteca_order.write({'tags': new_tags})

                    _logger.info(f"✓ Tags actualizados en Producteca: {new_tags}")
                    
                    # Agregar mensaje en la orden de Odoo
                    order.message_post(
                        body=f"🔄 <b>Sustituciones aplicadas en Producteca:</b><br/>"
                            f"• Productos sustituidos: {', '.join(substituted_products)}<br/>"
                            f"• Tags: {new_tags}<br/>"
                            f"• Sistema de protección activo",
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
                # Parsear tags para obtener productos sustituidos
                substituted_products = self._parse_substituted_products(producteca_order.tags)
                
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