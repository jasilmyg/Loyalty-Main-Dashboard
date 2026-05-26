import os
import pandas as pd
from django.core.management.base import BaseCommand
from analytics.models import ProductSale
from django.db import transaction

class Command(BaseCommand):
    help = 'Load product sales data from Excel files into PostgreSQL'

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+', type=str, help='Paths to the Excel files')

    def handle(self, *args, **options):
        for file_path in options['files']:
            if not os.path.exists(file_path):
                self.stdout.write(self.style.ERROR(f'File not found: {file_path}'))
                continue

            self.stdout.write(self.style.NOTICE(f'Loading data from {file_path}...'))
            
            try:
                # Use calamine for fast reading if available
                try:
                    df = pd.read_excel(file_path, engine='calamine')
                except ImportError:
                    df = pd.read_excel(file_path)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error reading {file_path}: {str(e)}'))
                continue

            df.columns = df.columns.str.strip()
            
            # Clean up numeric fields
            df['QTY'] = pd.to_numeric(df['QTY'], errors='coerce').fillna(0).astype(int)
            df['Sold Price'] = pd.to_numeric(df['Sold Price'], errors='coerce').fillna(0.0)
            
            # Parse dates enforcing European/Indian format DD/MM/YYYY
            df['Date'] = pd.to_datetime(df['Date'], format='mixed', dayfirst=True, errors='coerce').dt.date

            # Drop rows without a valid date or product
            df = df.dropna(subset=['Date', 'Product'])

            sales = []
            batch_size = 5000
            
            self.stdout.write(self.style.NOTICE(f'Parsed {len(df)} rows. Inserting into DB...'))
            
            for index, row in df.iterrows():
                sales.append(ProductSale(
                    date=row['Date'],
                    invoice_number=str(row.get('Invoice Number', '')),
                    branch=str(row.get('Branch', '')),
                    product=str(row.get('Product', '')),
                    category=str(row.get('Category', '')),
                    brand=str(row.get('Brand', '')),
                    qty=row['QTY'],
                    sold_price=row['Sold Price']
                ))

                # Batch insert
                if len(sales) >= batch_size:
                    with transaction.atomic():
                        ProductSale.objects.bulk_create(sales)
                    sales = []
                    self.stdout.write(f'Inserted {index + 1} records...')

            # Insert remaining
            if sales:
                with transaction.atomic():
                    ProductSale.objects.bulk_create(sales)
                self.stdout.write(f'Inserted final {len(sales)} records.')

            self.stdout.write(self.style.SUCCESS(f'Successfully loaded {file_path}'))
