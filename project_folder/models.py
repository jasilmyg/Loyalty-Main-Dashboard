from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import text

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Admin, Manager, Staff

# The sales_data table is already created by the pipeline.
# We will use text() queries or a mapped class for analytics.
