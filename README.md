# AI Traffic Violation

## Supabase setup

1. Run `supabase_schema.sql`, then `supabase_auth.sql`, in the Supabase SQL Editor. Re-run both after pulling schema changes; the scripts are designed to be repeatable. The current schema makes `violations.video_id` nullable because an image-based violation is linked through `image_analysis_id` instead.
2. Create private Storage buckets named `videos` and `violations`. Set each bucket's upload size limit within the limits of your Supabase plan. The video worker scales annotated results to at most 1120 pixels on the longest side and 12 FPS to keep them small enough for plans with a 50 MB file limit. Keep both buckets private; the app uploads with the server-only service-role key and grants access to processed videos through short-lived signed URLs. If upgrading existing public buckets, switch them to private; older public URLs will be renewed as private links when possible.
   For importing openly licensed sample traffic media from Wikimedia Commons, also create a private `traffic-dataset` bucket and follow [HUONG_DAN_SUPABASE_VA_WORKER.md](./HUONG_DAN_SUPABASE_VA_WORKER.md).
3. Configure these backend environment variables. Never expose `SUPABASE_SERVICE_ROLE_KEY` to the frontend or commit it:

   ```powershell
   $env:SUPABASE_URL = "https://<project-ref>.supabase.co"
   $env:SUPABASE_ANON_KEY = "<anon-key>"
   $env:SUPABASE_SERVICE_ROLE_KEY = "<service-role-key>"
   $env:SUPABASE_AUTH_REDIRECT_URL = "http://localhost:5000/"
   $env:CORS_ALLOWED_ORIGINS = "http://localhost:5000,http://127.0.0.1:5000"
   ```

   Add the redirect URL to Supabase **Authentication → URL Configuration → Redirect URLs**. Set `CORS_ALLOWED_ORIGINS` to the exact origins serving the frontend.
4. Start the API and the video worker in separate terminals from the repository root:

   ```powershell
   $env:SUPABASE_URL = "https://<project-ref>.supabase.co"
   $env:SUPABASE_ANON_KEY = "<anon-key>"
   $env:SUPABASE_SERVICE_ROLE_KEY = "<service-role-key>"
   python backend\app.py
   ```

   In a second terminal, set the same variables again before starting the worker:

   ```powershell
   $env:SUPABASE_URL = "https://<project-ref>.supabase.co"
   $env:SUPABASE_ANON_KEY = "<anon-key>"
   $env:SUPABASE_SERVICE_ROLE_KEY = "<service-role-key>"
   python backend\process_pending_videos.py --loop 5
   ```

   Replace the placeholders with current keys from the same Supabase project as `SUPABASE_URL`. `SUPABASE_ANON_KEY` should be that project's anon/publishable key; `SUPABASE_SERVICE_ROLE_KEY` should be its service-role/secret key, never the anon/publishable key. These PowerShell variables apply only to that terminal and child processes; set them in both terminals and restart both processes after changing them. Keep the service-role key server-side only. The API `/health` endpoint and top bar distinguish a missing service-role key from an invalid API key. `SUPABASE_INVALID_API_KEY` usually means a stale key, a key copied from another project, or the wrong key type assigned to an environment variable. Without a valid service-role key, image history, violation trends, private Storage evidence, and video queue processing are unavailable. Keep one worker running. It claims jobs atomically through `claim_next_video_job()`, updates progress, retries failures up to three attempts, and records a heartbeat. The API binds to `0.0.0.0:5000`; set `FLASK_DEBUG=1` only for local development.
5. Register the first account in the app. Public registration creates viewer accounts only. To promote the first administrator, run the following SQL with the registered email:

   ```sql
   UPDATE public.user_profiles SET role = 'admin' WHERE email = 'admin@example.com';
   UPDATE public.users SET role = 'admin' WHERE email = 'admin@example.com';
   UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
   WHERE email = 'admin@example.com';
   ```

   Sign out and sign in again to refresh the role. Administrators can create additional administrators from the app. If email confirmation is enabled, users must verify their email before signing in.

   If registration fails, check the backend log and Supabase **Authentication → Logs**. Confirm email signups are enabled, `SUPABASE_URL` and `SUPABASE_ANON_KEY` target the intended project, and `supabase_auth.sql` completed successfully. A database-trigger/unique-constraint error usually points to the `create_user_profile` trigger or a duplicate username/email in `user_profiles`; an already-registered email must sign in or use a different address.

Supabase Auth is the source of credentials; `public.user_profiles` is the application profile and role source, while `public.users` is retained as a compatibility mirror. Sessions are stored in backend memory, so users must sign in again after an API restart.

## Camera stop-line settings

The stop-line check can be switched on or off for each analysis. Give every physical camera a stable ID and save its enabled setting and line ratio; new analyses use those values when the matching camera is selected. Drag the line in the preview or use the slider to set its vertical position.

## Analysis history, evidence reports, and activity

Apply the latest `supabase_schema.sql` migration before enabling the new history and trend features. Image analyses and their original/annotated files are retained in the private `violations` Storage bucket. A viewer can browse only image analyses they submitted; administrators can browse all records. Image and video violation rows include a report action that creates short-lived signed evidence links and can be printed or saved as PDF. Keep `SUPABASE_SERVICE_ROLE_KEY` configured on the backend; it is required for private evidence and the trend RPC.

The dashboard trend chart supports daily and weekly buckets and an inclusive start/end date range. The admin-only activity page supports action-name and date filters. Both pages read the `audit_logs` table; ensure the migration has created it and that server-side writes are enabled.

Video uploads use Supabase's signed resumable TUS endpoint (`/storage/v1/upload/resumable/sign`) in 6 MB chunks, directly from the browser to the private `videos` bucket. The API returns a short-lived, object-scoped upload token; the service-role key remains on the backend. The upload progress reaches 100% when Storage has received the file, after which the app verifies the object and creates a video-processing job. Keep the Supabase Storage per-file limit above the selected video's size.

The stop-line preview can be moved vertically and rotated from -45° to +45°. The same angle is stored with camera settings and applied to image and video detection; 0° keeps the existing horizontal line. Apply the latest `supabase_schema.sql` to add the `stop_line_angle` columns to existing projects.

Video detections are consolidated into approximate events when the same violation type is observed within three seconds and either the bounding boxes overlap or the recognized plate matches. Reports preserve the first/last frame and number of merged observations. This is a heuristic, not persistent object tracking: a vehicle can be split into multiple records or nearby vehicles can occasionally be merged, so verify evidence before enforcement.

## Train and evaluate the helmet model

The helmet dataset YAML points to separate train, validation, and held-out test splits. Validate the dataset paths, label syntax, and per-class annotation counts before a run:

```powershell
venv\Scripts\python.exe train.py --validate-only
```

Train reproducibly with a fixed seed, deterministic mode, early stopping, and saved Ultralytics plots:

```powershell
venv\Scripts\python.exe train.py --epochs 50 --imgsz 640 --batch 8 --seed 42
```

By default, training uses the validation split for model selection and saves a separate validation evaluation. For an existing checkpoint, evaluate without retraining. Use the held-out test split only for a final candidate:

```powershell
venv\Scripts\python.exe train.py --model runs\detect\train-3\weights\best.pt --evaluate-only --evaluate-test
```

Each run creates a unique directory under `runs\detect` with `weights\best.pt`, Ultralytics plots/results, and `metrics_report.json` containing dataset/class counts, software/hardware details, training settings, and per-split precision, recall, mAP50, and mAP50-95 metrics. Keep the test split out of routine model tuning; compare runs using validation results and report the test results for the selected final model.

To use a trained checkpoint in the API/worker, set its path in the environment of both processes before starting them:

```powershell
$env:HELMET_MODEL_PATH = "D:\AI_Traffic_Violation\runs\detect\<run-name>\weights\best.pt"
```

The dataset metadata identifies its source and CC BY 4.0 license; preserve that attribution and verify dataset terms before redistributing images or trained weights.

## Operations and data

- The `/health` endpoint reports API, database, and worker status. If it reports `SUPABASE_WORKER_SCHEMA_MISSING`, apply the latest `supabase_schema.sql` in the Supabase SQL Editor; this means the service-role database connection works but the worker heartbeat table has not been installed. `/api/audit-logs` is available to administrators.
- `GET /image-analyses` lists the signed-in user's image-analysis history; `GET /image-analyses/<id>` returns an owned record and evidence. Administrators can access all image history.
- `GET /api/dashboard/trends?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&bucket=day|week` returns daily or weekly violation counts.
- `GET /violations/<id>/report` returns a violation profile with short-lived signed evidence URLs. The frontend report can be printed or saved as PDF.
- Processed videos and source uploads remain in private Storage. Short-lived signed links are issued only to authenticated users; expired links can be refreshed from the video list.
- Back up the Supabase database and both Storage buckets independently. Test restoring them periodically; the SQL schema does not back up uploaded media.
- Configure `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_AUTH_REDIRECT_URL`, and `CORS_ALLOWED_ORIGINS` in the deployment environment. Do not enable Flask debug mode in production.
