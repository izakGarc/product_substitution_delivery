# -*- coding: utf-8 -*-
from odoo import models, fields, api
import math

class ProductSubstituteLine(models.Model):
    _name = 'product.substitute.line'
    _description = 'Línea de Producto Sustituto'
    _order = 'sequence, id'

    sequence = fields.Integer(string='Secuencia', default=10)
    
    product_tmpl_id = fields.Many2one(
        'product.template', string='Producto Principal',
        required=True, ondelete='cascade'
    )
    substitute_product_id = fields.Many2one(
        'product.product', string='Producto Sustituto',
        required=True, domain="[('type', '=', 'product')]"
    )
    channel_id = fields.Many2one(
        'partner.channel', string='Canal', required=True
    )

    location_id = fields.Many2one(
        'stock.location',
        string='Ubicación',
        domain="[('usage', '=', 'internal')]",
        help='Si se especifica, los contadores de stock solo '
             'consideran esta ubicación. Si se deja vacío, '
             'se usa el stock global.'
    )

    substitute_code = fields.Char(
        related='substitute_product_id.default_code',
        string='Referencia Interna', readonly=True
    )
    substitute_name = fields.Char(
        related='substitute_product_id.name',
        string='Nombre', readonly=True
    )

    # A la mano: si hay ubicación, sumamos solo esos quants
    substitute_qty_available = fields.Float(
        string='A la Mano',
        compute='_compute_substitute_qty_available',
        store=False,
    )

    substitute_qty_reserved = fields.Float(
        string='Reservados',
        compute='_compute_substitute_qty_reserved',
        store=False,
    )

    substitute_qty_free = fields.Float(
        string='Disponible Libre',
        compute='_compute_substitute_qty_free',
        store=False,
    )

    # ── COMPUTES ACTUALIZADOS ──────────────────────────────

    def _get_kit_available_units(self):
        """
        Calcula cuántas unidades del KIT se pueden armar con el stock de componentes.
        Retorna None si el sustituto no es un KIT (sin BOM phantom).
        Retorna floor(min(free_component / qty_bom)) para cada componente si es KIT.
        """
        self.ensure_one()
        bom = self.env['mrp.bom'].search([
            ('product_tmpl_id', '=', self.substitute_product_id.product_tmpl_id.id),
            ('type', '=', 'phantom'),
        ], limit=1)

        if not bom:
            return None

        min_units = float('inf')
        for bom_line in bom.bom_line_ids:
            domain = [('product_id', '=', bom_line.product_id.id)]
            if self.location_id:
                domain += [('location_id', 'child_of', self.location_id.id)]
            quants = self.env['stock.quant'].search(domain)
            on_hand = sum(quants.mapped('quantity'))
            reserved = sum(quants.mapped('reserved_quantity'))
            free = on_hand - reserved
            if bom_line.product_qty > 0:
                units = math.floor(free / bom_line.product_qty)
            else:
                units = 0
            min_units = min(min_units, units)

        return int(min_units) if min_units != float('inf') else 0

    def _get_quants(self):
        """Devuelve los quants filtrados por producto y ubicación (si aplica)."""
        self.ensure_one()
        domain = [('product_id', '=', self.substitute_product_id.id)]
        if self.location_id:
            domain += [('location_id', 'child_of', self.location_id.id)]
        return self.env['stock.quant'].search(domain)

    @api.depends('substitute_product_id', 'location_id')
    def _compute_substitute_qty_available(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_available = 0
                continue
            kit_units = rec._get_kit_available_units()
            if kit_units is not None:
                rec.substitute_qty_available = kit_units
            elif rec.location_id:
                quants = rec._get_quants()
                rec.substitute_qty_available = sum(quants.mapped('quantity'))
            else:
                rec.substitute_qty_available = rec.substitute_product_id.qty_available

    @api.depends('substitute_product_id', 'location_id')
    def _compute_substitute_qty_reserved(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_reserved = 0
                continue
            kit_units = rec._get_kit_available_units()
            if kit_units is not None:
                rec.substitute_qty_reserved = 0
            else:
                quants = rec._get_quants()
                rec.substitute_qty_reserved = sum(quants.mapped('reserved_quantity'))

    @api.depends('substitute_product_id', 'location_id')
    def _compute_substitute_qty_free(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_free = 0
                continue
            kit_units = rec._get_kit_available_units()
            if kit_units is not None:
                rec.substitute_qty_free = kit_units
            else:
                quants = rec._get_quants()
                on_hand = sum(quants.mapped('quantity'))
                reserved = sum(quants.mapped('reserved_quantity'))
                rec.substitute_qty_free = on_hand - reserved