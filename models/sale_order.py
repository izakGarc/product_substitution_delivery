# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_order_channel(self):
        """
        Obtiene el canal de la orden de venta.
        
        Funciona en dos escenarios:
        1. Orden manual: el usuario asignó channel_ids → se usa directamente
        2. Orden de Producteca: channel_ids puede estar vacío al confirmar
           porque el compute aún no guardó → se detecta por nombre de la orden
        """
        # Escenario 1: canal ya asignado (orden manual o compute ya ejecutado)
        if self.channel_ids:
            channel = self.channel_ids[0]
            _logger.info(f"Canal obtenido de channel_ids: {channel.name}")
            return channel

        # Escenario 2: detectar canal por nombre de la orden
        # Mismo mapeo que usa _compute_channel en settings-loomber
        order_name = (self.name or '').lower()
        channel_name = ''

        if 'meli' in order_name:
            channel_name = 'Mercado Libre'
        elif 'wal' in order_name:
            channel_name = 'Walmart'
        else:
            mappings = {
                'comfu': 'Creditienda',
                'liverpool': 'liverpool',
                'yuhu': 'yuhu',
                'doto': 'doto',
                'coppel': 'coppel',
                'shein': 'shein',
            }
            for keyword, value in mappings.items():
                if keyword in order_name:
                    channel_name = value
                    break

        if channel_name:
            channel = self.env['partner.channel'].search(
                [('name', 'ilike', channel_name)], limit=1
            )
            if channel:
                _logger.info(f"Canal detectado por nombre de orden '{self.name}': {channel.name}")
                return channel
            else:
                _logger.warning(f"Canal '{channel_name}' no encontrado en partner.channel")

        _logger.warning(f"No se pudo determinar canal para orden: {self.name}")
        return None

    def _get_warehouse_from_location(self, location):
        """
        Dado un stock.location, retorna el stock.warehouse al que pertenece
        comparando con lot_stock_id de cada almacén.
        """
        warehouse = self.env['stock.warehouse'].search([
            ('lot_stock_id', '=', location.id)
        ], limit=1)
        if warehouse:
            _logger.info(f"  Ubicación '{location.name}' → Almacén: '{warehouse.name}'")
        else:
            _logger.warning(f"  No se encontró almacén para ubicación '{location.name}'")
        return warehouse

    def _get_product_available_qty_by_location(self, product, location):
        """
        Obtiene stock disponible libre en una ubicación específica.
        Usa stock.quant para calcular: a_la_mano - reservado.
        """
        try:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', 'child_of', location.id),
            ])
            on_hand = sum(quants.mapped('quantity'))
            reserved = sum(quants.mapped('reserved_quantity'))
            available = on_hand - reserved

            _logger.info(
                f"  [stock] {product.default_code or product.name} "
                f"@ {location.name}: "
                f"mano={on_hand} | reservado={reserved} | libre={available}"
            )
            return available

        except Exception as e:
            _logger.error(f"Error calculando stock para {product.default_code}: {e}")
            return 0

    def _find_substitute(self, product, required_qty, order_channel):
        """
        Busca el primer sustituto con stock suficiente para el canal dado.

        Lógica:
        - Filtra sustitutos por canal (incluye genéricos sin canal)
        - Recorre en orden de sequence
        - Verifica stock en la ubicación específica configurada del sustituto
        - Retorna (substitute_product, substitute_location) con el primero que alcance
        - Si ninguno tiene stock suficiente → retorna (None, None) → se deja el original

        Retorna: (product.product, stock.location) o (None, None)
        """
        substitute_lines = product.product_tmpl_id.delivery_substitute_line_ids.sorted('sequence')

        if order_channel:
            substitute_lines = substitute_lines.filtered(
                lambda l: l.channel_id.id == order_channel.id or not l.channel_id
            )
            _logger.info(f"  Sustitutos para canal '{order_channel.name}': {len(substitute_lines)}")
        else:
            substitute_lines = substitute_lines.filtered(lambda l: not l.channel_id)
            _logger.info(f"  Sin canal detectado - sustitutos genéricos: {len(substitute_lines)}")

        if not substitute_lines:
            _logger.info(f"  Sin sustitutos configurados para este canal")
            return None, None

        for idx, line in enumerate(substitute_lines, 1):
            substitute = line.substitute_product_id
            sub_location = line.location_id

            if not sub_location:
                _logger.warning(
                    f"  Sustituto #{idx} {substitute.default_code} "
                    f"sin ubicación configurada, saltando"
                )
                continue

            substitute_qty = self._get_product_available_qty_by_location(substitute, sub_location)
            canal_info = line.channel_id.name if line.channel_id else 'genérico'

            _logger.info(
                f"  → #{idx} (seq:{line.sequence}, {canal_info}): "
                f"{substitute.default_code} @ {sub_location.name} = {substitute_qty} disponibles"
            )

            if substitute_qty >= required_qty:
                _logger.info(f"  ✓ SELECCIONADO: {substitute.default_code}")
                return substitute, sub_location
            else:
                _logger.info(f"  ✗ Insuficiente ({substitute_qty} < {required_qty})")

        _logger.warning(f"  Ningún sustituto con stock suficiente → se mantiene producto original")
        return None, None

    def _get_order_warehouse_location(self, order):
        """Obtiene la ubicación lot_stock del almacén de la orden"""
        try:
            warehouse = order.warehouse_id
            if not warehouse:
                warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', order.company_id.id)
                ], limit=1)
                _logger.warning(f"Usando almacén por defecto: {warehouse.name if warehouse else 'NINGUNO'}")

            if warehouse and warehouse.lot_stock_id:
                _logger.info(f"Almacén de la orden: {warehouse.name} → {warehouse.lot_stock_id.name}")
                return warehouse.lot_stock_id
            else:
                raise UserError("No se pudo determinar el almacén para la orden")

        except Exception as e:
            _logger.error(f"Error obteniendo ubicación de almacén: {e}")
            return None

    def _process_product_substitutions(self):
        """
        Procesa sustituciones de productos antes de confirmar la orden.

        Lógica por línea de producto (no servicio):
        1. Verificar stock del producto original en la ubicación del almacén de la orden
        2. Si tiene stock suficiente → no sustituir
        3. Si NO tiene stock → buscar sustituto por canal (usando _find_substitute)
        4. Si hay sustituto disponible:
           a. Eliminar línea original
           b. Crear línea con el sustituto al mismo precio
           c. Cambiar almacén de la orden al del sustituto (si es diferente)
           d. Marcar en Producteca con tag SUBSTITUTED
        5. Si no hay sustituto con stock → dejar el producto original sin cambios

        NOTA: La lógica actual procesa solo el primer producto storable de la orden.
        Si en el futuro se manejan órdenes con múltiples productos storables,
        se deberá definir qué almacén aplica cuando cada sustituto resulta
        en un almacén diferente (actualmente gana el primero).
        """
        _logger.info("=== Iniciando procesamiento de sustituciones ===")

        for order in self:
            # Buscar orden de Producteca relacionada
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)

            if producteca_order:
                _logger.info(f"Orden Producteca detectada: {producteca_order.name}")

            # Obtener canal de la orden (manual o por nombre)
            order_channel = self._get_order_channel()

            # Obtener ubicación del almacén de la orden
            order_location = self._get_order_warehouse_location(order)
            if not order_location:
                _logger.error(f"No se pudo obtener ubicación para orden {order.name}")
                continue

            substituted_products = []
            lines_to_remove = []
            new_lines_to_create = []
            new_warehouse = None

            for line in order.order_line:
                # Ignorar líneas sin producto o de tipo servicio
                if not line.product_id:
                    continue
                if line.product_id.type == 'service':
                    _logger.info(f"Ignorando línea de servicio: {line.product_id.default_code or line.product_id.name}")
                    continue

                original_product = line.product_id
                original_qty = line.product_uom_qty
                original_price = line.price_unit
                original_sku = original_product.default_code or str(original_product.id)

                _logger.info(f"--- Procesando: {original_sku} x {original_qty} @ {order_location.name} ---")

                # PASO 1: Verificar stock del producto original
                original_stock = self._get_product_available_qty_by_location(
                    original_product, order_location
                )

                if original_stock >= original_qty:
                    _logger.info(
                        f"✓ Stock suficiente para {original_sku}: "
                        f"{original_stock} >= {original_qty} → SIN SUSTITUCIÓN"
                    )
                    continue

                _logger.info(
                    f"✗ Stock insuficiente para {original_sku}: "
                    f"{original_stock} < {original_qty} → BUSCANDO SUSTITUTO"
                )

                # PASO 2: Buscar sustituto
                substitute_product, substitute_location = self._find_substitute(
                    original_product, original_qty, order_channel
                )

                if not substitute_product:
                    _logger.warning(f"Sin sustituto disponible para {original_sku} → se mantiene original")
                    continue

                substitute_sku = substitute_product.default_code or str(substitute_product.id)
                _logger.info(f"SUSTITUCIÓN: {original_sku} → {substitute_sku} @ {substitute_location.name}")

                # PASO 3: Determinar almacén del sustituto
                substitute_warehouse = self._get_warehouse_from_location(substitute_location)

                if substitute_warehouse and substitute_warehouse.id != order.warehouse_id.id:
                    new_warehouse = substitute_warehouse
                    _logger.info(
                        f"Cambio de almacén requerido: "
                        f"{order.warehouse_id.name} → {substitute_warehouse.name}"
                    )

                # PASO 4: Verificar si el sustituto es kit (BOM Phantom)
                bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', substitute_product.product_tmpl_id.id),
                    ('type', '=', 'phantom')
                ], limit=1)

                if bom:
                    _logger.info(f"📦 BOM Phantom para {substitute_sku}")
                    total_price = original_price * original_qty
                    total_components_qty = sum(
                        bl.product_qty * original_qty for bl in bom.bom_line_ids
                    )
                    for bom_line in bom.bom_line_ids:
                        component = bom_line.product_id
                        component_qty = bom_line.product_qty * original_qty
                        component_price = (
                            total_price / total_components_qty
                            if total_components_qty > 0 else 0
                        )
                        new_lines_to_create.append({
                            'order_id': order.id,
                            'product_id': component.id,
                            'product_uom_qty': component_qty,
                            'product_uom': component.uom_id.id,
                            'price_unit': component_price,
                            'name': (
                                f"{component.name} (sustituto de {original_sku})"
                                if original_sku != component.default_code
                                else component.name
                            ),
                            'sequence': line.sequence,
                        })

                    order.message_post(
                        body=(
                            f"<b>SKU {original_sku}</b> sin stock en {order_location.name} "
                            f"({original_stock} disponibles, requiere {original_qty}).<br/>"
                            f"Sustituido por kit <b>{substitute_sku}</b>:<br/>"
                            f"• {len(bom.bom_line_ids)} componentes<br/>"
                            f"• Almacén sustituto: "
                            f"{substitute_warehouse.name if substitute_warehouse else substitute_location.name}"
                        ),
                        message_type='comment'
                    )
                else:
                    new_lines_to_create.append({
                        'order_id': order.id,
                        'product_id': substitute_product.id,
                        'product_uom_qty': original_qty,
                        'product_uom': substitute_product.uom_id.id,
                        'price_unit': original_price,
                        'name': (
                            f"{substitute_product.name} (sustituto de {original_sku})"
                            if original_sku != substitute_sku
                            else substitute_product.name
                        ),
                        'sequence': line.sequence,
                    })

                    order.message_post(
                        body=(
                            f"<b>SKU {original_sku}</b> sin stock en {order_location.name} "
                            f"({original_stock} disponibles, requiere {original_qty}).<br/>"
                            f"Sustituido por <b>SKU {substitute_sku}</b>:<br/>"
                            f"• Cantidad: {original_qty}<br/>"
                            f"• Precio: ${original_price:,.2f}<br/>"
                            f"• Ubicación sustituto: {substitute_location.name}<br/>"
                            f"• Almacén: "
                            f"{substitute_warehouse.name if substitute_warehouse else 'N/A'}"
                        ),
                        message_type='comment'
                    )

                substituted_products.append(original_sku)
                lines_to_remove.append(line)

            # PASO 5: Cambiar almacén de la orden si aplica
            if new_warehouse and new_warehouse.id != order.warehouse_id.id:
                old_warehouse_name = order.warehouse_id.name
                order.write({'warehouse_id': new_warehouse.id})
                _logger.info(f"Almacén actualizado: {old_warehouse_name} → {new_warehouse.name}")
                order.message_post(
                    body=(
                        f"🏭 <b>Almacén actualizado por sustitución:</b><br/>"
                        f"• Anterior: {old_warehouse_name}<br/>"
                        f"• Nuevo: {new_warehouse.name}"
                    ),
                    message_type='comment'
                )

            # PASO 6: Marcar en Producteca
            if producteca_order and substituted_products:
                try:
                    current_tags = producteca_order.tags or ''
                    substitution_tag = f"SUBSTITUTED:{','.join(substituted_products)}"

                    if current_tags and 'SUBSTITUTED:' not in current_tags:
                        new_tags = f"{current_tags};{substitution_tag}"
                    elif not current_tags:
                        new_tags = substitution_tag
                    else:
                        new_tags = current_tags

                    producteca_order.write({'tags': new_tags})
                    _logger.info(f"Tags Producteca: {new_tags}")

                    order.message_post(
                        body=(
                            f"🔄 <b>Sustituciones registradas en Producteca:</b><br/>"
                            f"• SKUs sustituidos: {', '.join(substituted_products)}<br/>"
                            f"• Sistema de protección activo"
                        ),
                        message_type='comment'
                    )
                except Exception as e:
                    _logger.error(f"Error marcando sustituciones en Producteca: {e}")

            # PASO 7: Eliminar líneas originales
            for line in lines_to_remove:
                _logger.info(f"Eliminando línea original: {line.product_id.default_code}")
                line.unlink()

            # PASO 8: Crear nuevas líneas con sustitutos
            for vals in new_lines_to_create:
                new_line = self.env['sale.order.line'].create(vals)
                _logger.info(
                    f"Línea creada: {new_line.product_id.default_code} "
                    f"x {new_line.product_uom_qty} @ ${new_line.price_unit:,.2f}"
                )

        _logger.info("=== Sustituciones completadas ===")

    def action_confirm(self):
        _logger.info(f"=== Confirmando orden {self.name} ===")
        self._process_product_substitutions()
        return super(SaleOrder, self).action_confirm()


class SaleOrderLineProtection(models.Model):
    _inherit = 'sale.order.line'

    @api.model
    def create(self, vals):
        """
        Bloquea la creación de líneas de productos ya sustituidos.
        Esto previene que Producteca sobreescriba las líneas sustituidas
        cuando re-sincroniza la orden.
        """
        if 'order_id' in vals and vals['order_id']:
            order = self.env['sale.order'].browse(vals['order_id'])
            producteca_order = self.env['producteca.sale_order'].search([
                ('sale_order', '=', order.id)
            ], limit=1)

            if (producteca_order and producteca_order.tags
                    and 'SUBSTITUTED:' in producteca_order.tags):
                substituted_products = self._parse_substituted_products(
                    producteca_order.tags
                )
                line_product = None
                if 'product_id' in vals and vals['product_id']:
                    product = self.env['product.product'].browse(vals['product_id'])
                    line_product = product.default_code

                if line_product and line_product in substituted_products:
                    _logger.info(
                        f"🛡️ LÍNEA BLOQUEADA - SKU sustituido: {line_product} "
                        f"en orden {order.name}"
                    )
                    order.message_post(
                        body=(
                            f"🛡️ <b>Creación de línea bloqueada</b><br/>"
                            f"• SKU: {line_product}<br/>"
                            f"• Razón: producto ya sustituido<br/>"
                            f"• Protección activa contra re-sincronización de Producteca"
                        ),
                        message_type='comment'
                    )
                    return self.env['sale.order.line']

        return super().create(vals)

    def _parse_substituted_products(self, tags):
        """Extrae lista de SKUs sustituidos del campo tags de Producteca"""
        substituted = []
        if not tags:
            return substituted
        for tag_part in tags.split(';'):
            if tag_part.startswith('SUBSTITUTED:'):
                products = tag_part.replace('SUBSTITUTED:', '').split(',')
                substituted.extend([p.strip() for p in products if p.strip()])
        return substituted