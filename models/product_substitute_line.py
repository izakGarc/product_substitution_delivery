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
        string='Cantidad Disponible',
        readonly=True
    )