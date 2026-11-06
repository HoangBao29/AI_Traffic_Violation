-- Run this file once in the Supabase SQL Editor to enable application accounts.

-- Keep the existing public.users table in sync with Supabase Auth.
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    email TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer'
);

-- Backfill accounts that were registered before this sync was installed.
INSERT INTO public.users (id, email, full_name, role)
SELECT
    auth_user.id,
    LOWER(auth_user.email),
    COALESCE(auth_user.raw_user_meta_data ->> 'full_name', ''),
    CASE WHEN auth_user.raw_app_meta_data ->> 'role' = 'admin' THEN 'admin' ELSE 'viewer' END
FROM auth.users AS auth_user
WHERE auth_user.email IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM public.users AS app_user
      WHERE LOWER(app_user.email) = LOWER(auth_user.email)
  )
ON CONFLICT (id) DO UPDATE
SET email = EXCLUDED.email,
    full_name = EXCLUDED.full_name;

CREATE TABLE IF NOT EXISTS public.user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer', 'admin')),
    badge_id TEXT NOT NULL DEFAULT 'VIEWER',
    unit TEXT NOT NULL DEFAULT 'Người dùng hệ thống',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS badge_id TEXT NOT NULL DEFAULT 'VIEWER';
ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT 'Người dùng hệ thống';

CREATE UNIQUE INDEX IF NOT EXISTS user_profiles_username_lower_idx
    ON public.user_profiles (LOWER(username));
CREATE UNIQUE INDEX IF NOT EXISTS user_profiles_email_lower_idx
    ON public.user_profiles (LOWER(email));

WITH existing_users AS (
    SELECT
        id,
        LOWER(email) AS email,
        LOWER(COALESCE(
            raw_user_meta_data ->> 'username',
            SPLIT_PART(email, '@', 1)
        )) AS username,
        COALESCE(raw_user_meta_data ->> 'full_name', '') AS full_name,
        CASE
            WHEN raw_app_meta_data ->> 'role' = 'admin' THEN 'admin'
            ELSE 'viewer'
        END AS role,
        ROW_NUMBER() OVER (
            PARTITION BY LOWER(COALESCE(
                raw_user_meta_data ->> 'username',
                SPLIT_PART(email, '@', 1)
            ))
            ORDER BY created_at, id
        ) AS username_order
    FROM auth.users
    WHERE email IS NOT NULL
)
INSERT INTO public.user_profiles (id, username, email, full_name, role)
SELECT
    id,
    CASE
        WHEN username_order = 1 THEN username
        ELSE LEFT(username, 25) || '_' || LEFT(id::TEXT, 6)
    END,
    email,
    full_name,
    role
FROM existing_users
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.create_user_profile()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    profile_username TEXT;
    base_username TEXT;
BEGIN
    base_username := LOWER(BTRIM(COALESCE(
        NULLIF(NEW.raw_user_meta_data ->> 'username', ''),
        SPLIT_PART(NEW.email, '@', 1)
    )));
    IF base_username = '' THEN
        base_username := 'user';
    END IF;
    profile_username := base_username;

    BEGIN
        INSERT INTO public.user_profiles (id, username, email, full_name, role)
        VALUES (
            NEW.id,
            profile_username,
            LOWER(NEW.email),
            COALESCE(NEW.raw_user_meta_data ->> 'full_name', ''),
            'viewer'
        );
    EXCEPTION WHEN unique_violation THEN
        IF NOT EXISTS (
            SELECT 1
            FROM public.user_profiles
            WHERE LOWER(username) = LOWER(profile_username)
              AND id <> NEW.id
        ) THEN
            RAISE;
        END IF;

        profile_username := LEFT(base_username, 19) || '_' ||
            LEFT(REPLACE(NEW.id::TEXT, '-', ''), 12);
        INSERT INTO public.user_profiles (id, username, email, full_name, role)
        VALUES (
            NEW.id,
            profile_username,
            LOWER(NEW.email),
            COALESCE(NEW.raw_user_meta_data ->> 'full_name', ''),
            'viewer'
        );
    END;

    BEGIN
        INSERT INTO public.users (id, email, full_name, role)
        VALUES (
            NEW.id,
            LOWER(NEW.email),
            COALESCE(NEW.raw_user_meta_data ->> 'full_name', ''),
            'viewer'
        )
        ON CONFLICT (id) DO UPDATE
        SET email = EXCLUDED.email,
            full_name = EXCLUDED.full_name;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'Could not sync legacy public.users for auth user % (SQLSTATE %): %',
            NEW.id, SQLSTATE, SQLERRM;
    END;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created_profile ON auth.users;
CREATE TRIGGER on_auth_user_created_profile
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.create_user_profile();

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.user_profiles TO authenticated;

CREATE OR REPLACE FUNCTION public.current_user_is_admin()
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.user_profiles
        WHERE id = (SELECT auth.uid()) AND role = 'admin'
    );
$$;
GRANT EXECUTE ON FUNCTION public.current_user_is_admin() TO authenticated;

DROP POLICY IF EXISTS "Users can read their own profile" ON public.user_profiles;
CREATE POLICY "Users can read their own profile"
    ON public.user_profiles FOR SELECT TO authenticated
    USING (id = (SELECT auth.uid()) OR public.current_user_is_admin());

-- Public signups always receive viewer access. Promote the first administrator
-- manually after registering, then let that administrator create future admins:
-- UPDATE public.user_profiles SET role = 'admin' WHERE email = 'admin@example.com';
-- UPDATE auth.users
-- SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
-- WHERE email = 'admin@example.com';
