"""Loopback-only teaching environment, not a production deployment."""
import os
SECRET_KEY = 'course-superset-local-only-not-for-production-20260930'
SQLALCHEMY_DATABASE_URI = 'sqlite:////app/superset_home/course.db'
FEATURE_FLAGS = {'ENABLE_TEMPLATE_PROCESSING': False}
WTF_CSRF_ENABLED = True
TALISMAN_ENABLED = False
ENABLE_PROXY_FIX = bool(os.environ.get('DW_BI_PUBLIC_URL'))
# The gateway makes browser cookies Secure. Keep loopback API/CSRF sessions usable.
SESSION_COOKIE_SAMESITE = 'Lax'
