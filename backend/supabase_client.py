from supabase import create_client

SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ha3N4cWhwZm9ia2FuZWJkb2htIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc1MjUzOTUsImV4cCI6MjEwMzEwMTM5NX0.fTyE3aH6k1OMus9DnNdh_Hcd9dMs3iFL9pfAqYbchDk"

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)