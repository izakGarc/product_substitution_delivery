{
    'name': 'Product Substitution on Delivery',
    'version': '16.0.2.0.0',
    'category': 'Inventory',
    'summary': 'Sustituye productos sin stock en la entrega por otros definidos desde el backend.',
    'description': """
Al validar una entrega (stock.picking), si un producto no tiene stock disponible:
- Busca un 'Producto Sustituto en Entrega' definido en la variante del producto (product.product).
- Si se encuentra un sustituto:
    - Si el sustituto tiene una Lista de Materiales (BOM) de tipo 'Phantom', se reemplaza el movimiento original por los componentes de la BOM.
    - Si el sustituto no tiene una BOM Phantom, se reemplaza el movimiento original por uno del producto sustituto.
- Si no se encuentra un sustituto, el movimiento original se deja sin cambios.
    """,
    'depends': [
        'stock',
        'sale', # <-- Dependencia agregada
        'product',
        'mrp', # <-- Dependencia agregada
        'odoo_connector_api_producteca', #Para que ya no se actualice sola.
        ],
    'author': 'ELIAS JAHAZIEL VILLEGAS AMARILLAS',
    'installable': True,
    'application': False,
    'auto_install': False,
    'data': [
        'views/product_view.xml'
    ],
    'license': 'LGPL-3', # Buena práctica añadir una licencia
}