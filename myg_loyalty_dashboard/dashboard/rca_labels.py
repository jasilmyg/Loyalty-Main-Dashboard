"""Manually reviewed ("read by meaning") labels for low-rated Google reviews.

Every review rated <= 3 stars that has a comment was read and given one ``card/item`` label (see
scratch/rca_label/GUIDE.md for the taxonomy).  The labels live in ``rca_labels.json`` as
``{review_key: "card/item"}`` where ``review_key`` = first 12 hex chars of md5(normalised comment).

Reviews rated 4-5 stars are still classified by the SQL rules in ``rca_rules.CLS_SQL`` (there are
~139k of them -- far too many to read).  A low-rated review that is not in the label file (for example a
brand-new one) falls back to the SQL rule result, so the dashboard never loses a review.
"""
import hashlib
import json
import os
import re

from django.core.cache import cache

from dashboard.rca_rules import CLS_SQL, REVIEWS_SRC
from dashboard.rca_v2_logic import DATE

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rca_labels.json')
_mem = {'mtime': None, 'labels': {}}


def review_key(comment):
    t = re.sub(r'\s+', ' ', comment or '').strip().lower()
    return hashlib.md5(t.encode('utf-8')).hexdigest()[:12]


def _fix_text(t):
    """Some backends return text columns hex-encoded; decode defensively."""
    if isinstance(t, (bytes, bytearray)):
        return bytes(t).decode('utf-8', 'replace')
    if t and len(t) >= 40 and len(t) % 2 == 0 and re.fullmatch(r'[0-9a-f]+', t):
        try:
            return bytes.fromhex(t).decode('utf-8')
        except Exception:
            return t
    return t or ''


def labels():
    try:
        mt = os.path.getmtime(_PATH)
    except OSError:
        return {}
    if _mem['mtime'] != mt:
        with open(_PATH, encoding='utf-8') as f:
            _mem['labels'] = json.load(f)
        _mem['mtime'] = mt
    return _mem['labels']


def labels_version():
    try:
        return str(int(os.path.getmtime(_PATH)))
    except OSError:
        return '0'


def get_low_rows(ch, where_store=''):
    """All reviews rated <= 3 (de-duplicated) with their FINAL ``cls`` ('card/item').

    ``where_store`` is '' or ' AND store_name IN (...)'.  Result is cached for 5 minutes and is
    shared by the card totals and the detail views, so both always agree.
    """
    ck = 'rca_lowrows_v1:{}:{}'.format(hashlib.md5(where_store.encode('utf-8')).hexdigest()[:10], labels_version())
    cached = cache.get(ck)
    if cached is not None:
        return cached
    lab = labels()
    res = ch.query(
        f"SELECT store_name, rating, comment, {DATE} AS d, dim_emotion, dim_suggested_action, {CLS_SQL} AS cls "
        f"FROM {REVIEWS_SRC} WHERE rating <= 3{where_store}").result_rows
    out = []
    for store, rating, comment, d, emo, act, rule_cls in res:
        comment = _fix_text(comment)
        cls = lab.get(review_key(comment)) if comment.strip() else None
        out.append({'store': store, 'rating': int(rating), 'comment': comment, 'd': d,
                    'emo': emo or '', 'act': act or '', 'cls': cls or rule_cls, 'manual': bool(cls)})
    cache.set(ck, out, 300)
    return out
