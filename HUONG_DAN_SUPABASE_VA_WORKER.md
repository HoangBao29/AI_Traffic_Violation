# Hướng dẫn cấu hình Supabase và worker video

## Trạng thái hiện tại của bạn

Kết quả health:

```json
{
  "anon_database": "ok",
  "api": "ok",
  "database": "ok",
  "error_code": "SUPABASE_WORKER_SCHEMA_MISSING",
  "service_role": "ok",
  "service_role_configured": true,
  "video_worker": "schema_missing"
}
```

Điều này có nghĩa API, anon key, service-role key và kết nối database đều hoạt động. Supabase chưa có bảng `public.worker_heartbeats`, nên API chưa thể đọc trạng thái worker.

## Bảng `worker_heartbeats` có cần thiết không?

Có, nếu bạn muốn sử dụng worker xử lý video và xem worker đang hoạt động hay không. Worker cập nhật thời điểm hoạt động gần nhất vào bảng này; API dùng thông tin đó để báo `ok`, `stale` hoặc `not_running`.

Bảng này **không chứa dữ liệu vi phạm hay thông tin đăng nhập**. Nó chỉ lưu tên worker, thời điểm heartbeat và một số thông tin vận hành. Bảng được bật Row Level Security (RLS); worker và API truy cập bằng service-role key ở backend.

Nếu bạn chỉ phân tích ảnh và không dùng xử lý video nền, ứng dụng vẫn có thể kết nối database, nhưng health sẽ tiếp tục báo thiếu schema heartbeat cho đến khi tạo bảng.

## Cách khuyến nghị: áp dụng schema của project

1. Mở file `supabase_schema.sql` trong thư mục gốc của project.
2. Trong Supabase Dashboard, mở **SQL Editor** và chọn **New query**.
3. Sao chép toàn bộ nội dung `supabase_schema.sql` vào SQL Editor rồi chọn **Run**.
4. Kiểm tra phần kết quả để chắc chắn không có câu lệnh báo lỗi.
5. Nếu SQL chạy thành công nhưng API vẫn báo thiếu bảng, chạy riêng câu lệnh sau trong SQL Editor để làm mới schema cache:

   ```sql
   NOTIFY pgrst, 'reload schema';
   ```

`supabase_schema.sql` tạo các bảng, hàm và chính sách mà các tính năng của project cần, không chỉ riêng bảng heartbeat. Nên dùng cách này khi chưa chắc các migration trước đã được áp dụng đầy đủ. Script được thiết kế để có thể chạy lại an toàn trong trường hợp bình thường.

## Chỉ tạo bảng heartbeat còn thiếu

Nếu bạn đã áp dụng các phần còn lại của `supabase_schema.sql` và chỉ thiếu bảng này, có thể chạy đoạn SQL sau trong Supabase **SQL Editor**:

```sql
CREATE TABLE IF NOT EXISTS public.worker_heartbeats (
    worker_name TEXT PRIMARY KEY,
    last_seen TIMESTAMPTZ NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE public.worker_heartbeats ENABLE ROW LEVEL SECURITY;
```

Sau đó chạy:

```sql
NOTIFY pgrst, 'reload schema';
```

Không tạo bảng thủ công trong Table Editor với cấu trúc khác; tên và kiểu cột cần khớp với schema trên.

## Khởi động lại và kiểm tra

Sau khi cập nhật schema:

1. Khởi động lại API (terminal đang chạy API: nhấn `Ctrl+C`, sau đó chạy lại lệnh khởi động API).
2. Mở `http://localhost:5000/health` trong trình duyệt.
3. Nếu bảng đã được tạo, `database` và `service_role` vẫn là `ok`. `video_worker` sẽ là `not_running` cho đến khi worker được khởi động.
4. Để xử lý video nền, mở terminal mới tại thư mục project và chạy:

   ```powershell
   cd D:\AI_Traffic_Violation
   .\venv\Scripts\python.exe backend\process_pending_videos.py --loop 5
   ```

   Terminal worker cần có cùng các biến môi trường Supabase như API, đặc biệt là `SUPABASE_SERVICE_ROLE_KEY`. Giữ terminal mở khi cần worker xử lý video. Không dán key vào tài liệu, mã nguồn, ảnh chụp hoặc chat.
5. Chờ worker cập nhật heartbeat, rồi tải lại `http://localhost:5000/health`. Khi worker đang hoạt động, trạng thái dự kiến là `"video_worker": "ok"`.

## Upload video dung lượng lớn và căn góc vạch dừng

Trang phân tích video dùng signed resumable TUS upload của Supabase Storage (`/storage/v1/upload/resumable/sign`) theo từng phần 6 MB, gửi trực tiếp từ trình duyệt vào bucket riêng `videos`. Yêu cầu gửi `x-signature` bằng token upload tạm thời do backend tạo; không gửi API key/service-role key trên luồng upload này. Khi thanh upload đạt 100%, tệp đã được gửi lên Storage; ứng dụng tiếp tục xác minh tệp và tạo công việc trong hàng đợi trước khi hiện tiến độ xử lý AI.

Nếu thấy `Supabase không khởi tạo được upload (HTTP 413)`, Storage đã từ chối kích thước khai báo ngay lúc tạo phiên upload; chưa có phần video nào được gửi và chưa có job phân tích. Với video khoảng 169,81 MB, vào Supabase Dashboard → **Storage → Buckets → videos → Edit bucket** và tăng **File size limit** lên ít nhất **200 MB**. Đồng thời kiểm tra **Storage → Configuration** (giới hạn kích thước tệp chung) và giới hạn theo gói/project; giới hạn chung hoặc gói dịch vụ phải cho phép tệp lớn hơn 169,81 MB. Lưu cấu hình rồi thử tải lại. Nếu gói hiện tại không cho phép giới hạn cần thiết, cần đổi gói hoặc dùng video nhỏ hơn; chia thành các chunk TUS không thể vượt giới hạn kích thước tối đa của một object.

API của dự án hiện nhận video tối đa 500 MB, nhưng giới hạn phía Supabase có thể thấp hơn và sẽ được áp dụng trước khi upload bắt đầu. Nếu đang dùng Supabase Free với giới hạn 50 MB, hãy nén video đầu vào xuống dưới giới hạn đó (nên để dư vài MB).

Sau khi AI phân tích xong, worker tạo video kết quả với cạnh dài tối đa 1120 px và tối đa 12 FPS, rồi dùng FFmpeg mã hóa H.264 (`yuv420p`, MP4 fast-start) để trình duyệt phát được. Cấu hình giảm đáng kể dung lượng video 4K; worker thử tăng mức nén nếu kết quả ban đầu vượt 45 MB. FFmpeg phải được cài trên máy chạy worker và có trong `PATH`; nếu chưa có trong `PATH`, đặt biến môi trường `FFMPEG_PATH` thành đường dẫn đầy đủ tới `ffmpeg.exe`. Worker chỉ tải kết quả lên khi tệp nhỏ hơn 45 MB để chừa khoảng an toàn cho giới hạn 50 MB. Trong giao diện, tiến độ 95–98% là bước mã hóa và lưu video kết quả/vi phạm lên Supabase; phiên bản mới hiển thị thêm 95%, 96% và 98% khi hoàn tất từng bước. Nếu tệp kết quả vẫn vượt 45 MB, worker ghi lỗi cụ thể vào bản ghi video thay vì chờ upload quá giới hạn.

Sau khi cập nhật mã worker, dừng worker cũ bằng `Ctrl+C` rồi khởi động lại để nạp phiên bản mới. Video đang ở trạng thái `processing` do worker cũ dừng sẽ được hàm hàng đợi thu hồi sau 30 phút; nếu cần chạy lại ngay, chỉ đặt lại đúng bản ghi video về `pending` sau khi chắc chắn worker cũ đã dừng.

Trong phần cấu hình vạch dừng, thanh **Góc xoay vạch dừng** cho phép căn đường từ -45° đến +45°; 0° là đường ngang như trước. Vị trí dọc và góc được lưu theo mã camera. Sau khi cập nhật ứng dụng, chạy lại `supabase_schema.sql` để thêm cột `stop_line_angle` cho bảng cấu hình camera và lịch sử ảnh; đặt cùng góc với vạch thật trong khung hình trước khi chạy phân tích.

## Ý nghĩa một số trạng thái health

| Trường | Giá trị | Ý nghĩa |
|---|---|---|
| `api` | `ok` | Flask API đang hoạt động. |
| `anon_database` | `ok` | Kết nối database bằng anon key hoạt động. |
| `database` | `ok` | Kết nối database bằng service-role key hoạt động. |
| `service_role` | `ok` | Service-role key đã được Supabase chấp nhận. |
| `service_role_configured` | `true` | Tiến trình API có nhận biến service-role; giá trị `true` tự nó chưa chứng minh key hợp lệ. |
| `video_worker` | `not_running` | Chưa có heartbeat của worker hoặc worker chưa chạy. |
| `video_worker` | `ok` | Worker đã gửi heartbeat gần đây. |
| `video_worker` | `stale` | Heartbeat cũ; kiểm tra terminal worker xem tiến trình còn chạy hay đã báo lỗi. |
| `video_worker` | `schema_missing` | Chưa có bảng `public.worker_heartbeats`; áp dụng schema ở các bước trên. |

## Nhập ảnh và video giao thông từ Wikimedia Commons

[Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Road_traffic) có nhiều ảnh về đường sá, nút giao, đèn tín hiệu và một số video giao thông. Kho video ít phong phú hơn kho ảnh và nội dung không được gắn nhãn vi phạm; đây là tư liệu minh họa/thử nghiệm, không phải dữ liệu kiểm định có nhãn chuẩn. Trước khi dùng hoặc chia sẻ, hãy xem lại từng trang tệp, giấy phép, ghi công và quyền riêng tư. Công cụ lưu tác giả, giấy phép và liên kết nguồn vào manifest; các giấy phép khác nhau có thể có điều kiện khác nhau.

### Chuẩn bị một lần

1. Trong Supabase **Storage**, tạo bucket `traffic-dataset` và để **Private**. Đặt giới hạn dung lượng bucket phù hợp với giới hạn mỗi tệp bên dưới.
2. Bucket `videos` cũng cần tồn tại và ở trạng thái Private. Bảng `videos` cần được tạo bằng `supabase_schema.sql`.
3. API key service-role chỉ được dùng ở backend. Nếu key từng bị gửi/chia sẻ, hãy thu hồi hoặc rotate key cũ trước; nhập key mới tại terminal, không ghi vào mã nguồn hoặc tài liệu.

## Chạy website của project

Giao diện frontend được Flask phục vụ trực tiếp; không cần chạy một frontend server riêng.

1. Mở terminal PowerShell mới, đặt thư mục project và URL thật của Supabase:

   ```powershell
   cd D:\AI_Traffic_Violation
   $env:SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
   $env:SUPABASE_ANON_KEY = Read-Host "Nhập anon/publishable key"
   $secureKey = Read-Host "Nhập service-role key mới" -AsSecureString
   $env:SUPABASE_SERVICE_ROLE_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
   Remove-Variable secureKey
   .\venv\Scripts\python.exe backend\app.py
   ```

   Nếu Supabase project của bạn khác, thay URL mẫu bằng URL của project đó. Giữ terminal mở khi sử dụng ứng dụng.
2. Mở trình duyệt tại [http://localhost:5000/](http://localhost:5000/). Đây là website; `/health` chỉ là trang kiểm tra trạng thái API.
3. Nếu muốn xử lý video nền, mở terminal thứ hai, đặt lại `SUPABASE_URL`, `SUPABASE_ANON_KEY` và `SUPABASE_SERVICE_ROLE_KEY` như trên rồi chạy:

   ```powershell
   cd D:\AI_Traffic_Violation
   .\venv\Scripts\python.exe backend\process_pending_videos.py --loop 5
   ```

   Worker là tiến trình riêng, cần giữ terminal mở khi muốn xử lý video. Không cần worker nếu chỉ mở website hoặc phân tích ảnh.
4. Khi muốn dừng API hoặc worker, nhấn `Ctrl+C` trong terminal tương ứng.

```powershell
$env:SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
$env:SUPABASE_ANON_KEY = (Read-Host "Nhập anon/publishable key của project này").Trim()

$secureKey = Read-Host "Nhập service-role/secret key của project này" -AsSecureString
$env:SUPABASE_SERVICE_ROLE_KEY = ([System.Net.NetworkCredential]::new("", $secureKey).Password).Trim()
Remove-Variable secureKey

.\venv\Scripts\python.exe backend\app.py
```
chạy video thì thêm terminal dưới đây:

Set-Location D:\AI_Traffic_Violation
$env:SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
$secureKey = Read-Host "Nhập service-role/secret key của project này" -AsSecureString
$env:SUPABASE_SERVICE_ROLE_KEY = ([System.Net.NetworkCredential]::new("", $secureKey).Password).Trim()
Remove-Variable secureKey
.\venv\Scripts\python.exe backend\process_pending_videos.py --loop 5
### Xem trước kết quả trước khi upload

Từ PowerShell tại thư mục project:

```powershell
cd D:\AI_Traffic_Violation
.\venv\Scripts\python.exe backend\import_wikimedia_commons.py --dry-run --images 5 --videos 3
```

Lệnh này chỉ tìm và liệt kê tên tệp, giấy phép, trang nguồn; không cần key Supabase và không upload.

### Upload ảnh và video

Trong cùng terminal PowerShell, lấy **Project URL thật** tại Supabase Dashboard → **Project Settings → API**. Thay ví dụ dưới đây bằng URL của project bạn, rồi nhập service-role key mới bằng lời nhắc bảo vệ (không đưa key trực tiếp vào câu lệnh):

```powershell
$env:SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
$secureKey = Read-Host "Nhập service-role key mới" -AsSecureString
$env:SUPABASE_SERVICE_ROLE_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
.\venv\Scripts\python.exe backend\import_wikimedia_commons.py --images 0 --videos 20
Remove-Item Env:SUPABASE_SERVICE_ROLE_KEY
```

Nếu API đang chạy, có thể xem Project URL hiện đang cấu hình trong terminal API bằng cách in riêng biến URL (URL không phải secret); không in hoặc chụp `SUPABASE_SERVICE_ROLE_KEY`.

Mặc định công cụ tìm tối đa 20 ảnh (20 MB mỗi ảnh) và 3 video (100 MB mỗi video). Đây là số lượng tối đa lấy từ kết quả tìm kiếm theo các từ khóa giao thông, sau khi lọc loại tệp, dung lượng và metadata giấy phép; không phải 20 ảnh/3 video đầu tiên trong toàn Wikimedia Commons. Kết quả có thể thay đổi theo dữ liệu tìm kiếm của Commons. Có thể thay đổi số lượng/giới hạn, ví dụ:

```powershell
.\venv\Scripts\python.exe backend\import_wikimedia_commons.py --images 50 --videos 5 --max-video-mb 150
```

Giới hạn tệp phải phù hợp với giới hạn bucket và gói Supabase của bạn. Công cụ lọc phần mở rộng, kích thước và metadata giấy phép; kết quả tìm kiếm tự động vẫn cần được con người xem lại. Để tuân thủ hướng dẫn CDN của Wikimedia, ảnh được tải bằng thumbnail rộng tối đa 1280 px thay vì file ảnh gốc; Wikimedia có thể cung cấp thumbnail từ `thumb.wikimedia.org` hoặc `upload.wikimedia.org`, cả hai host HTTPS này đều được importer cho phép. Video vẫn tải file media. Công cụ cách nhau tối thiểu 5 giây giữa các lần tải media. Có thể chạy lại: ảnh cùng page ID sẽ dùng cùng đường dẫn Storage và được ghi đè thay vì tạo object trùng; video được bỏ qua nếu đường dẫn đã có bản ghi trong bảng `videos`. Nếu lần chạy trước upload tệp video lên Storage nhưng thất bại trước khi tạo bản ghi database, lần chạy sau có thể ghi đè object đó rồi thử tạo bản ghi lại.

Thông báo `N mục nhập đầy đủ` là số ảnh đã upload thành công cộng video đã upload và thêm được vào database. Nếu số lỗi lớn, đọc các dòng `ERROR` phía trên để xác định nguyên nhân. Khi có 0 mục nhập đầy đủ, thường không có ảnh được lưu thành công; tuy nhiên, nếu lỗi xảy ra sau bước upload video nhưng trước khi tạo bản ghi database, object video có thể còn trong bucket `videos` mà chưa được đưa vào hàng đợi. Manifest chỉ được tạo khi có ít nhất một mục nhập hoàn chỉnh.

Trong lúc chạy, chờ các dòng `Đang tìm...`, `Đang nạp thư viện Supabase...` và `[n/tổng] Đang upload...` hoàn tất. Không nhấn `Ctrl+C` khi Python còn đang khởi tạo hoặc đang upload. `KeyboardInterrupt` nghĩa là tiến trình Python đã bị ngắt (thường do nhấn `Ctrl+C`); nếu traceback dừng ở `from supabase import create_client`, script chưa gọi Supabase và chưa upload tệp nào. Phiên bản hiện tại chỉ nạp SDK Supabase sau khi tìm được media, đồng thời kiểm tra URL trước khi bắt đầu tải.

Nếu thấy `HTTP Error 429: Too Many Requests`, Wikimedia Commons đang giới hạn tần suất truy cập; đây không phải lỗi Supabase. Thông báo CDN có thể yêu cầu dùng thumbnail thay vì file gốc; importer dùng thumbnail 1280 px và cho phép đúng hai host Wikimedia nói trên. Video vẫn cần tải file media nên có thể tiếp tục bị giới hạn. Khi CDN trả 429, importer dừng các lượt tải tiếp theo để tránh làm tình trạng nặng hơn; chờ ít nhất 15 phút rồi chỉ thử số lượng nhỏ. API tìm kiếm chờ tối thiểu một giây giữa yêu cầu và tự thử lại lỗi bận/giới hạn với thời gian chờ tăng dần; với HTTP 429 ở API, script tôn trọng header `Retry-After`. Hãy để tiến trình tiếp tục khi đang chờ. Nếu cần nhập ảnh trước, bỏ qua video bằng:

```powershell
.\venv\Scripts\python.exe backend\import_wikimedia_commons.py --images 5 --videos 0
```

Trong PowerShell, dấu nhắc `>>` có nghĩa terminal đang chờ phần tiếp theo của một lệnh chưa hoàn chỉnh. Nếu thấy `>>` bất ngờ, nhấn `Ctrl+C` một lần để hủy dòng nhập dở, đợi dấu nhắc `PS D:\AI_Traffic_Violation>` rồi nhập từng lệnh sạch như các ví dụ; không dán kèm phần traceback hoặc ký tự thừa.

### Vị trí dữ liệu sau khi nhập

- Ảnh nằm trong bucket `traffic-dataset`, theo thư mục `images/wikimedia/`.
- Manifest ghi tên tệp, tác giả, giấy phép, URL nguồn và đường dẫn Storage nằm trong `traffic-dataset/manifests/`.
- Video nằm trong bucket `videos`, theo thư mục `dataset/wikimedia/`, đồng thời được thêm vào bảng `videos` với trạng thái `pending`. Worker đang chạy sẽ đưa video vào xử lý.
- Ảnh nhập vào **không** được ghi giả thành lịch sử phân tích. Muốn xem trong lịch sử, hãy tải ảnh xuống và gửi phân tích qua ứng dụng; chỉ kết quả phân tích thực tế mới được lưu thành lịch sử.
- Video Commons có thể ở định dạng WebM/OGV. Nếu worker/OpenCV không hỗ trợ codec của một tệp cụ thể, bản ghi video đó sẽ báo lỗi xử lý; tệp gốc vẫn nằm trong Storage để kiểm tra hoặc tải về.

Việc nhận diện của mô hình trên tư liệu Commons có thể sai và không phải bằng chứng xác nhận vi phạm. Không dùng kết quả tự động làm căn cứ xử phạt nếu chưa được người có thẩm quyền kiểm tra.

## Phân tích hàng loạt ảnh đã nhập

Ảnh trong `traffic-dataset` chưa tự động được phân tích. Để đưa nhiều ảnh vào đúng quy trình phân tích của ứng dụng và lưu lịch sử từng ảnh:

1. Khởi động Flask API như trong mục **Chạy website của project** và giữ terminal API mở. Đăng nhập được tài khoản ứng dụng (email đã xác minh nếu yêu cầu xác minh đang bật).
2. Mở PowerShell terminal mới, vào thư mục project, đặt cùng Project URL và service-role key mới:

   ```powershell
   cd D:\AI_Traffic_Violation
   $env:SUPABASE_URL = "https://maksxqhpfobkanebdohm.supabase.co"
   $secureKey = Read-Host "Nhập service-role key mới" -AsSecureString
   $env:SUPABASE_SERVICE_ROLE_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
   Remove-Variable secureKey
   .\venv\Scripts\python.exe backend\analyze_commons_images.py --limit 25
   Remove-Item Env:SUPABASE_SERVICE_ROLE_KEY
   ```

3. Nhập email và mật khẩu tài khoản ứng dụng khi được hỏi. Mật khẩu không hiển thị khi nhập. Script lấy ảnh từ `traffic-dataset/images/wikimedia/`, tải từng ảnh vào bộ nhớ tạm, rồi gọi API `/detect/image` tuần tự; các kết quả thành công được lưu theo đúng tài khoản trong **Lịch sử phân tích ảnh**.
4. Mặc định mỗi lần chạy xử lý tối đa 25 ảnh, ảnh đã có trong lịch sử của tài khoản được bỏ qua. Có thể đặt `--limit` từ 1 đến 500. Dùng `--reanalyze` nếu thực sự muốn phân tích lại các ảnh đã có; thao tác đó sẽ tạo lịch sử phân tích mới.
5. Khi hoàn tất, mở website tại `http://localhost:5000/` và vào **Lịch sử phân tích ảnh**. Script chỉ xử lý ảnh Commons đã nhập trong đúng thư mục nói trên; không xử lý video.

Ví dụ xử lý tối đa 100 ảnh:

```powershell
.\venv\Scripts\python.exe backend\analyze_commons_images.py --limit 100
```

Việc phân tích chạy tuần tự qua cùng detector/API của ứng dụng, không chạy đồng thời để tránh làm quá tải máy. Thời gian tùy số ảnh và phần cứng. Nếu API chưa chạy, đăng nhập thất bại, hoặc service-role key không hợp lệ, script sẽ báo lỗi. Mỗi ảnh thành công được lưu riêng; lỗi của một ảnh không ngăn các ảnh còn lại tiếp tục. Ảnh Commons là tư liệu thử nghiệm không có nhãn chuẩn; cần kiểm tra thủ công mọi kết quả, không dùng tự động làm căn cứ xử phạt.
