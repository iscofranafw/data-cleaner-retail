"""Optional external barcode search, no untrusted website names auto-applied."""
import re
import requests


def lookup_openfoodfacts(barcode, timeout=12):
    """Only queries Open Food Facts when operator explicitly chooses online lookup."""
    code = str(barcode or '').strip()
    if not re.fullmatch(r'\d{8,14}', code):
        return {'status':'SKIPPED','reason':'Barcode tidak valid atau kosong'}
    try:
        r = requests.get('https://world.openfoodfacts.org/api/v2/product/'+code+'.json',
              headers={'User-Agent':'OlseraDataCleaner/0.2 (catalog audit; no automatic updates)'},
              timeout=timeout)
        if r.status_code == 404:
            return {'status':'NOT_FOUND'}
        r.raise_for_status()
        data = r.json()
        product = data.get('product') or {}
        if data.get('status') != 1:
            return {'status':'NOT_FOUND'}
        name = str(product.get('product_name') or product.get('product_name_id') or '').strip()
        brand = str(product.get('brands') or '').strip()
        quantity = str(product.get('quantity') or '').strip()
        if not name:
            return {'status':'NOT_FOUND'}
        return {'status':'FOUND','name':name,'brand':brand,'quantity':quantity,
                'source':'Open Food Facts','url':'https://world.openfoodfacts.org/product/'+code}
    except (requests.RequestException,ValueError,TypeError):
        return {'status':'ERROR','reason':'Sumber publik tidak dapat diakses'}
