from supabase_client import supabase

response = (
    supabase
    .table("videos")
    .select("*")
    .execute()
)

print(response.data)