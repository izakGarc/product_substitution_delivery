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
    
    allow_mix_substitutes = fields.Boolean(
        string='Permitir Mezclar Sustitutos',
        default=False,
        help='Si está marcado, cuando un sustituto no tenga stock suficiente, '
             'el sistema buscará el siguiente sustituto para completar la cantidad faltante. '
             'Si está desmarcado, solo usará el primer sustituto con stock >= cantidad requerida.'
    )