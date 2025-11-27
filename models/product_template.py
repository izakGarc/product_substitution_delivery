# -*- coding: utf-8 -*-
from odoo import models, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    delivery_substitute_line_ids = fields.One2many(
        'product.substitute.line',
        'product_tmpl_id',
        string='Productos Sustitutos para Entregas',
        help='Lista de productos sustitutos ordenados por prioridad. '
             'Usa las flechitas para reordenar.'
    )
    
    
    # # Campo viejo para compatibilidad (opcional - puedes eliminarlo)
    # delivery_substitute_id = fields.Many2one(
    #     'product.product',
    #     string='Producto Sustituto para Entregas (OBSOLETO)',
    #     domain="[('type', '=', 'product')]"
    # )