# HƯỚNG DẪN ĐỌC VÀ GIẢI THÍCH CHƯƠNG 3, 4, 5

Tài liệu này giúp bạn hiểu mối liên hệ giữa cơ sở lý thuyết ở Chương 3, thiết kế hệ thống ở Chương 4 và kết quả thực hiện ở Chương 5. Nội dung được giải thích theo cách dễ hình dung, đồng thời phân biệt rõ phần nào là mô hình AI, phần nào là thuật toán xử lý ảnh/luật nghiệp vụ và số liệu nào đã được đo thực tế.

> **Ý chính cần nhớ:** Hệ thống không dùng một mô hình duy nhất để tự kết luận tất cả vi phạm. YOLO phát hiện đối tượng; chương trình kết hợp các phát hiện đó với luật hình học, xử lý màu, OCR và quy trình lưu trữ. Các metric trong Chương 5 chỉ đánh giá detector mũ bảo hiểm, không phải toàn bộ hệ thống.

## 1. Bản đồ ba chương

| Chương | Trả lời câu hỏi | Nội dung của dự án |
|---|---|---|
| **Chương 3 – Cơ sở lý thuyết và yêu cầu** | Bài toán là gì, cần những kỹ thuật nào, hệ thống cần làm gì? | Thị giác máy tính, YOLO, bounding box, confidence, IoU, OCR, HSV, luật hình học; vai trò Viewer/Admin và yêu cầu xử lý ảnh/video. |
| **Chương 4 – Phân tích và thiết kế** | Các yêu cầu đó được ghép thành hệ thống như thế nào? | Frontend, Flask API, Supabase, mô hình AI, video worker, cơ sở dữ liệu, luồng xử lý và phân quyền. |
| **Chương 5 – Kết quả thực hiện** | Đã xây dựng/đánh giá được gì và bằng chứng là gì? | Metric detector mũ bảo hiểm, cấu hình đánh giá, giao diện, lịch sử, video, hồ sơ vi phạm và kiểm thử chức năng. |

Có thể nhớ bằng một câu:

> **Chương 3 nêu vấn đề và kiến thức → Chương 4 mô tả cách xây dựng lời giải → Chương 5 đưa ra kết quả và bằng chứng.**

## 2. Chương 3: hiểu bài toán và các khái niệm

### 2.1. Hệ thống nhận đầu vào gì?

Đầu vào của dự án là ảnh hoặc video giao thông do người dùng tải lên. Video được xem như một chuỗi frame (khung hình). Mỗi lần phân tích cũng có thể có cấu hình, chẳng hạn có bật kiểm tra vạch dừng hay không và đường dừng nằm ở vị trí nào trên ảnh.

Với một đối tượng được phát hiện, YOLO thường trả về:

- **Bounding box**: hình chữ nhật bao quanh đối tượng, thường viết `[x1, y1, x2, y2]`.
- **Class**: tên lớp, ví dụ `person`, `motorcycle`, `With Helmet`.
- **Confidence**: điểm tin cậy của dự đoán.

Ví dụ, box `[100, 80, 220, 300]` biểu diễn một vùng từ tọa độ `(100,80)` tới `(220,300)`. Confidence `0,80` là điểm của dự đoán đó theo mô hình; không nên hiểu là lời cam kết rằng kết luận ngoài đời đúng chính xác 80% trong mọi điều kiện.

### 2.2. YOLO, confidence, IoU và NMS

- **YOLO** là mô hình phát hiện đối tượng: từ ảnh đầu vào, nó dự đoán vị trí và lớp của các đối tượng.
- **Confidence threshold** là ngưỡng lọc dự đoán. Dự đoán dưới ngưỡng có thể bị loại trước khi dùng tiếp. Tăng ngưỡng thường làm kết quả thận trọng hơn nhưng có thể bỏ sót đối tượng; giảm ngưỡng có thể giữ thêm đối tượng nhưng tăng dự đoán nhầm.
- **IoU (Intersection over Union)** đo độ chồng lấn của hai bounding box:

  ```text
  IoU = diện tích phần giao nhau / diện tích phần hợp của hai box
  ```

  IoU bằng 1 nghĩa là hai box trùng hoàn toàn; IoU bằng 0 nghĩa là không giao nhau.
- **NMS (Non-Maximum Suppression)** là bước hậu xử lý thường dùng để bỏ các box dự đoán trùng nhau, giữ lại những box phù hợp hơn.

Confidence và IoU là hai ý khác nhau: confidence nói về điểm tin cậy của dự đoán; IoU nói về mức khớp vị trí giữa box dự đoán và box chuẩn.

### 2.3. Những kỹ thuật hỗ trợ suy luận

- **Geometric matching**: dùng vị trí/kích thước box để ghép người với xe máy phù hợp.
- **HSV**: biểu diễn màu theo Hue, Saturation, Value; có thể dùng khoảng màu để tìm pixel đỏ, xanh, vàng trong vùng đèn.
- **ROI (Region of Interest)**: vùng ảnh được chọn để xử lý tập trung, ví dụ vùng đèn hoặc vùng ước lượng biển số.
- **OCR (Optical Character Recognition)**: đọc ký tự từ ảnh và trả về chuỗi văn bản.
- **Rule-based detection**: kết luận theo điều kiện logic đã lập trình thay vì một mô hình học sâu tự học toàn bộ hành vi.
- **Frame sampling**: chọn một phần frame của video để xử lý, giảm chi phí tính toán nhưng có thể bỏ qua sự kiện rất ngắn.
- **Event merging**: gộp nhiều lần phát hiện gần nhau thành một sự kiện; đây là phép gộp gần đúng nếu không có tracker định danh phương tiện xuyên suốt video.

## 3. Chương 4: cách hệ thống thực sự chạy

### 3.1. Các thành phần và trách nhiệm

| Thành phần | Nói đơn giản | Trách nhiệm chính |
|---|---|---|
| Frontend HTML/CSS/JavaScript | Màn hình người dùng nhìn thấy | Đăng nhập, gửi tệp, chọn thiết lập, xem dashboard, hồ sơ và bằng chứng. |
| Flask API | Cửa tiếp nhận yêu cầu | Kiểm tra đầu vào/quyền, gọi detector cho ảnh, lưu và đọc dữ liệu. |
| AI detector | Bộ phân tích một ảnh/frame | Gọi YOLO, áp dụng luật hình học/đèn đỏ, gọi OCR khi cần và tạo ảnh chú thích. |
| Video worker | Tiến trình nền riêng | Nhận job video, giải mã/lấy mẫu frame, gộp sự kiện và cập nhật trạng thái. |
| Supabase Auth/PostgreSQL | Tài khoản và cơ sở dữ liệu | Xác thực người dùng, lưu hồ sơ, cấu hình, video, vi phạm, lịch sử và nhật ký. |
| Supabase Storage | Kho tệp riêng tư | Lưu ảnh/video bằng chứng; backend tạo URL ký có thời hạn để xem. |

Worker chạy tách khỏi request web vì video có thể mất nhiều thời gian. API nhận video và đưa vào hàng đợi; worker xử lý sau đó. Nếu worker không chạy, bản ghi vẫn có thể nằm chờ hoặc thất bại, dù web và Supabase vẫn hoạt động.

### 3.2. Pipeline phân tích ảnh từng bước

1. Người dùng chọn ảnh, chọn mã camera và bật/tắt vạch dừng.
2. Frontend gửi ảnh và thiết lập tới API `/detect/image`.
3. API kiểm tra định dạng, kích thước/tham số và quyền người dùng.
4. Detector tạo bản sao ảnh để vẽ kết quả, rồi chạy YOLO COCO để phát hiện người, xe máy, ô tô, xe buýt, xe tải và đèn giao thông.
5. Nếu bật kiểm tra đường dừng, detector xét màu vùng đèn bằng HSV. Vạch dừng hiện là đường ngang đặt theo `stop_line_ratio` của chiều cao ảnh.
6. YOLO tùy chỉnh phát hiện hai lớp mũ bảo hiểm `With Helmet` và `Without Helmet`.
7. Thuật toán hình học ghép người với xe máy. Nếu một xe được ghép với ít nhất 3 người, hệ thống tạo phát hiện chở quá người.
8. Với phát hiện phù hợp, hệ thống có thể ước lượng vùng biển số từ phần dưới box phương tiện, tiền xử lý ảnh và dùng EasyOCR. OCR có thể không đọc được hoặc đọc nhầm.
9. Detector trả về danh sách phát hiện, summary và ảnh đã chú thích.
10. API lưu ảnh gốc, ảnh chú thích, lượt phân tích và các vi phạm liên quan vào Supabase; trình duyệt hiển thị bằng chứng bằng URL ký tạm.

### 3.3. Pipeline video

1. API lưu video và tạo bản ghi có trạng thái `pending`.
2. Worker claim job từ Supabase; trạng thái chuyển sang `processing`.
3. OpenCV đọc video theo từng frame. Mặc định `frame_skip = 3`, tức là suy luận xấp xỉ một frame trong ba frame.
4. Mỗi frame được chọn đi qua pipeline ảnh. Frame không được chọn vẫn được ghi vào video đầu ra nhưng không nhất thiết có box AI.
5. Các phát hiện cùng loại được gộp nếu ở gần nhau về thời gian và box có IoU đủ lớn hoặc biển số đọc được giống nhau.
6. Worker tải video chú thích lên Storage, lưu các sự kiện vi phạm và cập nhật trạng thái/tiến độ.

Đây **không phải** multi-object tracking. Hệ thống có thể gộp nhầm xe gần nhau hoặc tách một xe thành nhiều sự kiện; nên kiểm tra video và bằng chứng trước khi duyệt.

### 3.4. Ba loại vi phạm không được tạo ra theo cùng một cách

| Loại | Phần phát hiện/suy luận | Điểm cần thận trọng |
|---|---|---|
| Không đội mũ bảo hiểm | YOLO custom phát hiện lớp `Without Helmet`. | Metric đánh giá ở Chương 5 liên quan trực tiếp đến detector này, nhưng chưa tự động đo chất lượng toàn bộ luồng xử lý. |
| Chở quá số người | YOLO COCO phát hiện người/xe; heuristic ghép hình học đếm người; từ 3 người trở lên tạo phát hiện. | Kết quả phụ thuộc vào box người/xe và phép ghép; confidence hồ sơ hiện lấy từ phát hiện xe, không phải xác suất tổng hợp cho kết luận vi phạm. |
| Vượt đèn đỏ | YOLO COCO tìm đèn/xe; HSV suy luận màu đèn; so sánh đáy box xe với đường ngang cấu hình. | Đây là luật trên ảnh/frame, không xác minh đầy đủ quỹ đạo xe vừa đi qua vạch. Bật đường dừng ở đoạn đường không phù hợp có thể tạo suy luận sai. |

### 3.5. Biển số và ý nghĩa của “OCR”

OCR là bước đọc ký tự, không đồng nghĩa với việc hệ thống có một detector chuyên biệt tìm đúng box biển số. Trong triển khai hiện tại, ROI được ước lượng từ bounding box phương tiện, thường ở phần thấp của xe. ROI được chuyển grayscale, phóng đại nếu nhỏ, tăng tương phản rồi đưa cho EasyOCR.

Vì vùng biển số có thể rất nhỏ, mờ, nghiêng hoặc bị che, kết quả có thể rỗng, sai ký tự hoặc hiện “Chưa rõ”. Nên gọi đây là **nhận dạng biển số hỗ trợ**, không cam kết đọc đúng mọi biển số.

## 4. Chương 5: hiểu quá trình huấn luyện và đánh giá

### 4.1. Ba tập dữ liệu dùng để làm gì?

- **Train**: dữ liệu để mô hình điều chỉnh trọng số.
- **Validation (val)**: dùng trong quá trình phát triển để theo dõi/chọn checkpoint và thiết lập.
- **Test**: tập đánh giá cuối được giữ riêng trong quy trình, nhằm ước lượng kết quả trên dữ liệu chưa dùng trực tiếp để chọn mô hình.

Trong dữ liệu dự án, mô hình custom có hai lớp: `With Helmet`, `Without Helmet`. Bộ dataset có 4.169 ảnh; báo cáo validator liệt kê 3.648 train, 372 val và 149 test. Tệp nhãn rỗng có thể đại diện ảnh không có đối tượng cần gán nhãn; không nhất thiết là lỗi. Test có một annotation trùng đã được validator loại khỏi tổng hợp annotation hợp lệ.

### 4.2. Huấn luyện khác với suy luận và đánh giá

- **Huấn luyện (training)**: đưa ảnh và nhãn train qua mô hình để cập nhật trọng số.
- **Suy luận (inference)**: dùng trọng số đã huấn luyện để dự đoán trên một ảnh/video thực tế.
- **Đánh giá (evaluation)**: so sánh dự đoán với nhãn chuẩn trên val/test để tính metric.

Run `train-3` ghi 50 epoch, ảnh 640 × 640, batch 8, seed 0, deterministic bật, patience 100 và resume từ `last.pt`. Checkpoint được đánh giá là `best.pt`. Báo cáo metrics tạo ngày 24/10/2026 có `run_type = evaluation_only`, tức là **lần chạy báo cáo không huấn luyện lại**, chỉ đánh giá checkpoint sẵn có.

### 4.3. Các con số của dataset

| Split | Ảnh | With Helmet | Without Helmet | Annotation hợp lệ | Nhãn rỗng | Annotation trùng |
|---|---:|---:|---:|---:|---:|---:|
| Train | 3.648 | 3.729 | 3.771 | 7.500 | 13 | 0 |
| Validation | 372 | 338 | 322 | 660 | 1 | 0 |
| Test | 149 | 170 | 103 | 273 | 0 | 1 |
| **Tổng** | **4.169** | **4.237** | **4.196** | **8.433** | **14** | **1** |

“Ảnh” và “annotation” là hai đơn vị khác nhau: một ảnh có thể chứa nhiều box, nên tổng annotation có thể lớn hơn số ảnh.

### 4.4. Giải thích metric bằng ngôn ngữ thông thường

**Precision** hỏi: “Trong các dự đoán dương tính mà mô hình đưa ra, có bao nhiêu dự đoán khớp nhãn chuẩn?”

```text
Precision = TP / (TP + FP)
```

**Recall** hỏi: “Trong các đối tượng thật có trong nhãn chuẩn, mô hình tìm được bao nhiêu?”

```text
Recall = TP / (TP + FN)
```

Trong đó:

- `TP` (true positive): dự đoán khớp đúng đối tượng/nhãn.
- `FP` (false positive): dự đoán nhầm hoặc không khớp theo tiêu chuẩn đánh giá.
- `FN` (false negative): đối tượng có trong nhãn nhưng mô hình bỏ sót.

Ví dụ minh họa, không phải số đếm của dataset dự án: nếu mô hình dự đoán 100 đối tượng dương tính và 84 trong số đó khớp, Precision là 84%. Nếu nhãn chuẩn có 100 đối tượng và mô hình tìm được 88, Recall là 88%.

**AP (Average Precision)** tóm tắt đường Precision–Recall cho một lớp. **mAP** là trung bình AP theo lớp tại các điều kiện IoU đã chọn.

- **mAP@0.5**: một box chỉ được ghép đúng nếu đúng lớp và IoU đạt ít nhất 0,5.
- **mAP@0.5:0.95**: tính mAP tại các IoU 0,50, 0,55, …, 0,95 rồi lấy trung bình. Ngưỡng cao yêu cầu box ôm sát đối tượng hơn.

Do đó mAP@0.5:0.95 thường thấp hơn mAP@0.5. Khoảng cách giữa hai chỉ số ở dự án cho thấy khi yêu cầu box chính xác vị trí nghiêm ngặt hơn, điểm số giảm. Không nên diễn giải mAP như “phần trăm ảnh nhận dạng đúng”.

### 4.5. Kết quả đánh giá thực tế được lưu

| Split | Precision | Recall | mAP@0.5 | mAP@0.5:0.95 |
|---|---:|---:|---:|---:|
| Validation (372 ảnh) | 85,96% | 90,48% | 93,45% | 58,96% |
| Test (149 ảnh) | 83,96% | 88,42% | 91,94% | 57,73% |

Trên test, Precision thấp hơn validation 2,00 điểm phần trăm; Recall thấp hơn 2,06 điểm; mAP@0.5 thấp hơn 1,51 điểm; mAP@0.5:0.95 thấp hơn 1,23 điểm. Hai split có kết quả tương đối gần nhau trong dataset này, nhưng điều đó **không đảm bảo** hiệu năng tương tự trên mọi camera, góc nhìn, điều kiện sáng hoặc loại phương tiện.

Thời gian ghi trong báo cáo test là tiền xử lý 2,11 ms + inference 6,94 ms + hậu xử lý 2,14 ms, tổng ba thành phần khoảng **11,19 ms/ảnh** trên RTX 4050 Laptop GPU. Đây không phải thời gian toàn request web: chưa tính upload, đọc/ghi Storage, database, khởi động mô hình và các bước ngoài đo lường đó.

### 4.6. Metric nào đang đánh giá cái gì?

Các con số ở bảng trên đo detector mũ bảo hiểm trên dataset hai lớp. Chúng **không đo trực tiếp**:

- độ chính xác của YOLO COCO trên dữ liệu camera của dự án;
- độ chính xác ghép người với xe máy/chở quá người;
- độ chính xác suy luận màu đèn và vị trí vạch;
- tỷ lệ đọc đúng biển số của EasyOCR;
- tỷ lệ video event bị gộp/tách nhầm;
- độ chính xác tổng thể của toàn bộ hệ thống.

Muốn đánh giá các phần đó, cần tạo bộ dữ liệu kiểm thử riêng có nhãn chuẩn cho từng tác vụ và định nghĩa metric phù hợp. Ví dụ: precision/recall cho từng loại vi phạm; tỷ lệ đọc biển số chính xác; hoặc tỷ lệ event được gộp đúng.

## 5. Cách nối nội dung ba chương khi thuyết trình

Bạn có thể trình bày theo đoạn mẫu sau:

> “Ở Chương 3, em xác định bài toán nhận ảnh/video giao thông và trình bày các kiến thức cần dùng như phát hiện đối tượng YOLO, bounding box, confidence, IoU, OCR, HSV và xử lý theo luật. Sang Chương 4, các kiến thức này được ghép thành ứng dụng web: Flask nhận yêu cầu, YOLO phát hiện đối tượng, các luật hình học và xử lý HSV suy luận một số vi phạm, OCR hỗ trợ đọc biển số; video được chuyển cho worker xử lý nền và dữ liệu được lưu ở Supabase. Ở Chương 5, em trình bày giao diện/chức năng đã xây dựng và đánh giá định lượng checkpoint YOLO mũ bảo hiểm trên tập validation/test. Các metric này chỉ phản ánh detector mũ bảo hiểm, không đại diện cho toàn bộ pipeline.”

Nếu được hỏi **“AI có chắc chắn kết luận vi phạm không?”**, câu trả lời phù hợp là:

> “Không. Hệ thống tự động phát hiện để hỗ trợ sàng lọc. Kết quả còn phụ thuộc chất lượng ảnh, góc camera, box phát hiện và các heuristic; người có thẩm quyền cần kiểm tra bằng chứng trước khi duyệt.”

Nếu được hỏi **“Vì sao mAP@0.5 cao mà mAP@0.5:0.95 thấp hơn?”**, có thể trả lời:

> “mAP@0.5 chấp nhận box có IoU từ 0,5, còn mAP@0.5:0.95 lấy trung bình từ ngưỡng 0,5 tới 0,95. Khi IoU cao, box phải định vị sát vật thể hơn nên tiêu chuẩn nghiêm hơn và điểm thường thấp hơn.”

Nếu được hỏi **“83,96% có phải độ chính xác toàn hệ thống không?”**, trả lời:

> “Không. Đó là Precision của detector mũ bảo hiểm trên split test theo quy trình đánh giá đã ghi. Chưa có một metric tổng hợp được xác minh cho toàn bộ hệ thống.”

## 6. Những cách diễn đạt nên tránh

| Không nên nói | Nên nói |
|---|---|
| “AI chính xác 91,94% trên toàn hệ thống.” | “Detector mũ bảo hiểm đạt mAP@0.5 91,94% trên tập test.” |
| “Hệ thống theo dõi chính xác từng phương tiện trong video.” | “Hệ thống lấy mẫu frame và gộp phát hiện gần nhau bằng heuristic thời gian, IoU hoặc biển số.” |
| “AI nhận dạng chắc chắn xe vừa vượt vạch.” | “Hệ thống suy luận theo màu đèn và vị trí box phương tiện so với đường ngang cấu hình.” |
| “Biển số luôn được nhận diện tự động.” | “EasyOCR hỗ trợ đọc ROI biển số ước lượng; kết quả có thể chưa rõ hoặc sai.” |
| “11,19 ms là thời gian hoàn thành phân tích trên web.” | “11,19 ms/ảnh là tổng các giai đoạn preprocess/inference/postprocess do báo cáo đánh giá ghi, không phải end-to-end.” |
| “Có thể thêm worker là đã chứng minh hệ thống mở rộng tốt.” | “Thiết kế tách worker tạo khả năng mở rộng; hiệu năng mở rộng cần benchmark riêng.” |

## 7. Ghi chú đối chiếu báo cáo và dữ liệu

1. Chương 3 mô tả một số công thức/kỹ thuật tổng quát. Khi nói về phần triển khai, ưu tiên các điều kiện và trọng số trong code thật; không khẳng định công thức khái niệm là chính xác nguyên văn code.
2. Dataset YAML khai báo split train/val/test. Nếu chưa kiểm tra ảnh trùng hoặc gần trùng giữa split, có thể gọi là “split test” thay vì khẳng định độc lập tuyệt đối.
3. Báo cáo metrics cuối là chế độ evaluation-only. Không nhầm ngày tạo báo cáo đánh giá với ngày huấn luyện.
4. Metric detector mũ bảo hiểm không đo các luật vượt đèn, phép ghép người–xe, OCR hoặc hiệu quả worker.
5. Trong lúc kiểm thử ứng dụng, đã quan sát thấy một số hồ sơ vi phạm cũ trả về `created_at = null`; khi dữ liệu thời gian thiếu, danh sách có thể hiện “—” và biểu đồ xu hướng không phản ánh đúng các hồ sơ đó. Migration cập nhật cột `created_at` trong `supabase_schema.sql` cần được chạy trên Supabase trước khi dùng dữ liệu này làm kết quả báo cáo.
6. Không mô tả kiểm thử video là thành công nếu worker chưa hoạt động hoặc job chưa hoàn tất. Trạng thái API/Supabase tốt không đồng nghĩa worker video đang chạy.

## 8. Bảng thuật ngữ tra nhanh

| Thuật ngữ | Nghĩa ngắn |
|---|---|
| AI / Deep Learning | Mô hình học từ dữ liệu để đưa ra dự đoán; trong dự án, YOLO custom được huấn luyện cho hai lớp mũ. |
| Computer Vision | Kỹ thuật xử lý và hiểu ảnh/video bằng máy tính. |
| Inference | Chạy mô hình đã huấn luyện để lấy dự đoán trên dữ liệu mới. |
| Bounding box | Hình chữ nhật bao quanh đối tượng. |
| Confidence | Điểm tin cậy của một dự đoán riêng lẻ. |
| IoU | Tỷ lệ giao nhau giữa box dự đoán và box chuẩn. |
| Precision | Tỷ lệ dự đoán dương tính khớp đúng. |
| Recall | Tỷ lệ đối tượng chuẩn được tìm thấy. |
| AP / mAP | Diện tích dưới đường Precision–Recall cho một lớp / trung bình theo lớp. |
| ROI | Vùng ảnh được chọn để tập trung xử lý. |
| OCR | Đọc ký tự từ ảnh thành văn bản. |
| Heuristic | Quy tắc/ước lượng thực dụng, không phải bảo đảm đúng cho mọi trường hợp. |
| Worker | Tiến trình nền xử lý tác vụ dài như video. |
| Signed URL | Đường dẫn tạm có thời hạn để truy cập tệp riêng tư. |
| Ground truth | Nhãn chuẩn đã được con người xác định để làm căn cứ đánh giá. |

