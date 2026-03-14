# -*- coding: utf-8 -*-
from odoo import models, fields, api

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
            if rec.location_id:
                quants = rec._get_quants()
                rec.substitute_qty_available = sum(quants.mapped('quantity'))
            else:
                # Sin ubicación → usar el campo estándar de Odoo
                rec.substitute_qty_available = rec.substitute_product_id.qty_available

    @api.depends('substitute_product_id', 'location_id')
    def _compute_substitute_qty_reserved(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_reserved = 0
                continue
            quants = rec._get_quants()
            rec.substitute_qty_reserved = sum(quants.mapped('reserved_quantity'))

    @api.depends('substitute_product_id', 'location_id')
    def _compute_substitute_qty_free(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_free = 0
                continue
            quants = rec._get_quants()
            on_hand = sum(quants.mapped('quantity'))
            reserved = sum(quants.mapped('reserved_quantity'))
            rec.substitute_qty_free = on_hand - reserved