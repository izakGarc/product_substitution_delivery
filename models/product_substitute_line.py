# -*- coding: utf-8 -*-
from odoo import models, fields, api

class ProductSubstituteLine(models.Model):
    _name = 'product.substitute.line'
    _description = 'Línea de Producto Sustituto'
    _order = 'sequence, id'

    sequence = fields.Integer(
        string='Secuencia',
        default=10,
        help='Determina la prioridad. Menor número = mayor prioridad'
    )
    
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Producto Principal',
        required=True,
        ondelete='cascade'
    )
    
    substitute_product_id = fields.Many2one(
        'product.product',
        string='Producto Sustituto',
        required=True,
        domain="[('type', '=', 'product')]"
    )
    
    substitute_code = fields.Char(
        related='substitute_product_id.default_code',
        string='Referencia Interna',
        readonly=True
    )
    
    substitute_name = fields.Char(
        related='substitute_product_id.name',
        string='Nombre',
        readonly=True
    )
    
    substitute_qty_available = fields.Float(
        related='substitute_product_id.qty_available',
        string='A la Mano', 
        readonly=True
    )

    substitute_qty_reserved = fields.Float(
        string='Reservados', 
        compute='_compute_substitute_qty_reserved',
        store=False
    )

    substitute_qty_free = fields.Float(
        string='Disponible Libre',
        compute='_compute_substitute_qty_free',
        help='Cantidad realmente disponible para usar (A la mano - Reservados)',
        store=False
    )

    @api.depends('substitute_product_id')
    def _compute_substitute_qty_reserved(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_reserved = 0
                continue

            # Obtener los quants de ese producto
            quants = self.env['stock.quant'].search([
                ('product_id', '=', rec.substitute_product_id.id),
            ])

            # Sumar la cantidad reservada
            rec.substitute_qty_reserved = sum(quants.mapped('reserved_quantity'))

    @api.depends('substitute_product_id', 'substitute_product_id.qty_available', 'substitute_product_id.outgoing_qty')  # ← NUEVO
    def _compute_substitute_qty_free(self):
        for rec in self:
            if not rec.substitute_product_id:
                rec.substitute_qty_free = 0
                continue
            
            # Stock libre = A la mano - Salientes
            qty_on_hand = rec.substitute_product_id.qty_available
            outgoing = rec.substitute_product_id.outgoing_qty
            rec.substitute_qty_free = qty_on_hand - outgoing
