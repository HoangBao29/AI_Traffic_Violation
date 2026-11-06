import os

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://maksxqhpfobkanebdohm.supabase.co")
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ha3N4cWhwZm9ia2FuZWJkb2htIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc1MjUzOTUsImV4cCI6MjEwMzEwMTM5NX0.fTyE3aH6k1OMus9DnNdh_Hcd9dMs3iFL9pfAqYbchDk"

SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", SUPABASE_KEY)

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


def create_auth_client():
    """Return an isolated client so sign-in does not mutate the shared DB client."""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_admin_client():
    """Return a service-role client for trusted server-side user administration."""
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service_role_key:
        return None
    return create_client(SUPABASE_URL, service_role_key)