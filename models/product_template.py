from odoo import models, fields

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    delivery_substitute_id = fields.Many2one(
        'product.product',
        string='Producto Sustituto para Entregas',
        domain="[('type', '=', 'product')]"
    )