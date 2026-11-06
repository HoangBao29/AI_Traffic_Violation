# KẾT LUẬN VÀ HƯỚNG PHÁT TRIỂN

## 1. Kết luận

Đề tài **“Xây dựng hệ thống phân tích vi phạm giao thông bằng trí tuệ nhân tạo”** tập trung xây dựng một ứng dụng web hỗ trợ tiếp nhận ảnh/video giao thông, phát hiện một số dấu hiệu vi phạm và quản lý kết quả phân tích. Hệ thống kết hợp giao diện HTML, CSS, JavaScript với backend Flask, dịch vụ Supabase và các thành phần xử lý ảnh, thị giác máy tính [1]–[6].

Về nghiệp vụ, hệ thống hỗ trợ đăng ký tài khoản người xem, đăng nhập, phân quyền viewer/admin, phân tích ảnh và đưa video vào hàng đợi xử lý nền. Người dùng có thể xem lịch sử phân tích ảnh, video, danh sách vi phạm, dashboard và báo cáo bằng chứng. Quản trị viên có thể tạo thêm tài khoản admin, cập nhật trạng thái duyệt vi phạm và xem nhật ký hoạt động. Ảnh gốc, ảnh đã chú thích và video được lưu trong Supabase Storage; metadata và kết quả được lưu trong PostgreSQL.

Về xử lý AI, hệ thống sử dụng YOLO để phát hiện đối tượng và nhận diện mũ bảo hiểm; dùng các phép tính hình học để liên kết người với xe máy; phân tích màu đèn bằng OpenCV cùng tùy chọn đường dừng; và sử dụng EasyOCR để hỗ trợ đọc biển số. Video được xử lý bởi worker riêng, có cập nhật tiến độ và lưu các sự kiện vi phạm gần trùng theo heuristic. Các kỹ thuật này cho thấy khả năng tích hợp mô hình thị giác máy tính và xử lý ảnh vào một quy trình ứng dụng có lưu trữ, tra cứu và hỗ trợ người dùng kiểm tra bằng chứng [2]–[5].

Trong quá trình thực hiện, đề tài giúp củng cố kiến thức về phát triển backend và API, thiết kế cơ sở dữ liệu, xác thực và phân quyền, lưu trữ tệp, xử lý ảnh/video, tích hợp mô hình học sâu, kiểm thử và vận hành các tiến trình nền. Hệ thống hiện là nền tảng thử nghiệm và hỗ trợ sàng lọc; kết quả AI không thay thế việc kiểm tra của con người và không nên được sử dụng độc lập làm căn cứ xử phạt.

## 2. Hạn chế

Bên cạnh các chức năng đã xây dựng, hệ thống còn một số hạn chế cần được xem xét:

- **Dữ liệu và đánh giá:** Tập dữ liệu hiện có tập trung vào hai lớp mũ bảo hiểm. Ảnh/video giao thông nhập từ nguồn bên ngoài chủ yếu phục vụ thử nghiệm vận hành và không mặc nhiên có nhãn chuẩn. Chưa có bộ kết quả đánh giá đã xác minh cho toàn bộ pipeline trong tài liệu hiện tại; do đó không đưa ra kết luận định lượng về độ chính xác tổng thể. Các chỉ số precision, recall, mAP cần được trích từ đúng lần đánh giá mô hình và nêu rõ tập dữ liệu/split.
- **Vượt đèn đỏ:** Logic hiện dựa vào vùng đèn giao thông được phát hiện, phân tích màu theo HSV và đường dừng ngang do người dùng cấu hình. Ánh sáng, phản xạ, độ phân giải, góc camera và phối cảnh có thể ảnh hưởng đến kết quả. Hệ thống chưa tự nhận dạng hình học đầy đủ của vạch đường tại mọi kiểu giao lộ.
- **Liên kết đối tượng và gộp sự kiện:** Việc ghép người với xe máy dựa trên đặc trưng hình học trong từng frame. Gộp sự kiện video dựa trên khoảng thời gian, độ chồng lấp bounding box hoặc biển số nhận dạng được, không phải theo dõi danh tính phương tiện liên tục. Vì vậy có khả năng bỏ sót, tách một sự kiện thành nhiều hồ sơ hoặc gộp nhầm các phát hiện gần nhau.
- **Nhận dạng biển số:** OCR phụ thuộc vào kích thước, độ nét, góc nhìn, ánh sáng và mức độ che khuất của biển số. Kết quả có thể không đọc được hoặc cần con người xác nhận.
- **Phụ thuộc vận hành:** Xử lý video cần worker đang chạy; các tính năng lịch sử, bằng chứng riêng tư và thống kê cần cấu hình Supabase đúng và duy trì khả năng truy cập dịch vụ. Phiên ứng dụng lưu trong bộ nhớ backend nên người dùng phải đăng nhập lại sau khi API khởi động lại.
- **Phạm vi kiểm thử và mở rộng:** Cần tiếp tục kiểm thử trên nhiều camera, điều kiện thời tiết/ánh sáng và quy mô dữ liệu lớn hơn; đồng thời đánh giá tải đồng thời, độ trễ xử lý, khả năng phục hồi worker và an toàn dữ liệu trong môi trường triển khai thực tế.

## 3. Hướng phát triển

Trong thời gian tới, hệ thống có thể được phát triển theo các hướng sau:

1. **Xây dựng bộ dữ liệu có nhãn đa dạng và có thể kiểm chứng:** Bổ sung ảnh/video được gán nhãn cho các tình huống không đội mũ, chở quá số người, vượt đèn đỏ và biển số; đa dạng hóa camera, thời tiết, thời điểm trong ngày và góc quan sát. Chia train/validation/test độc lập, quản lý nguồn và giấy phép, rồi công bố quy trình đánh giá rõ ràng.
2. **Đánh giá định lượng theo từng bài toán:** Báo cáo precision, recall, mAP và ma trận nhầm lẫn cho detector; đánh giá tỷ lệ đọc đúng biển số; đo tỷ lệ phát hiện đúng sự kiện trên video, thời gian xử lý mỗi frame và tỷ lệ lỗi. Cần có ground truth được gán nhãn độc lập và phân tích lỗi thay vì chỉ dựa vào số phát hiện của hệ thống.
3. **Cải thiện phát hiện vượt đèn đỏ:** Nghiên cứu định vị vạch dừng theo vùng/perspective thay vì một đường ngang, hiệu chỉnh camera, kết hợp trạng thái đèn theo thời gian và quỹ đạo xe, đồng thời cho phép lưu nhiều vùng giao thông cho từng camera.
4. **Tăng độ ổn định theo dõi video:** Bổ sung thuật toán multi-object tracking để duy trì ID phương tiện giữa các frame, giảm phụ thuộc vào IoU và OCR khi gộp sự kiện; tối ưu frame sampling và tận dụng GPU khi phần cứng cho phép.
5. **Nâng cao OCR biển số:** Cải thiện bước phát hiện vùng biển số, tiền xử lý ảnh, mô hình nhận dạng ký tự và hậu xử lý theo định dạng biển số; lưu mức tin cậy và yêu cầu người duyệt xác nhận kết quả không chắc chắn.
6. **Hoàn thiện vận hành và bảo mật:** Bổ sung giám sát worker, cảnh báo job bị treo, hàng đợi có khả năng mở rộng, chính sách lưu trữ/xóa dữ liệu, sao lưu và phục hồi. Tiếp tục rà soát RLS, quyền truy cập Storage, thời hạn signed URL, quản lý khóa và nhật ký thay đổi.
7. **Cải thiện khả năng giải thích và quy trình duyệt:** Hiển thị rõ bằng chứng, frame liên quan, vùng phát hiện, confidence và lý do hệ thống tạo sự kiện; duy trì bước kiểm tra của quản trị viên/CSGT trước khi chốt hồ sơ. Phản hồi duyệt có thể được dùng để phân tích lỗi và cải thiện hệ thống sau khi có quy trình quản trị dữ liệu phù hợp.

# TÀI LIỆU THAM KHẢO

[1] Pallets. (2026). *Flask Documentation (3.1.x)*. https://flask.palletsprojects.com/en/stable/ (truy cập ngày 29/10/2026).

[2] Ultralytics. (2026). *Ultralytics YOLO Documentation*; *Model Training with Ultralytics YOLO*. https://docs.ultralytics.com/ và https://docs.ultralytics.com/modes/train/ (truy cập ngày 29/10/2026).

[3] Bradski, G. (2000). The OpenCV Library. *Dr. Dobb’s Journal of Software Tools*. Tài liệu thư viện: https://docs.opencv.org/ (truy cập ngày 29/10/2026).

[4] JaidedAI. (2024). *EasyOCR: Ready-to-use OCR with 80+ supported languages*. GitHub repository, phiên bản 1.7.2. https://github.com/JaidedAI/EasyOCR (truy cập ngày 29/10/2026).

[5] Lin, T.-Y., Maire, M., Belongie, S., Hays, J., Perona, P., Ramanan, D., Dollár, P., & Zitnick, C. L. (2014). Microsoft COCO: Common Objects in Context. In *Computer Vision – ECCV 2014*, pp. 740–755. https://doi.org/10.1007/978-3-319-10602-1_48.

[6] Supabase. (2026). *Supabase Auth Documentation*; *Supabase Storage Documentation*; *Row Level Security*. https://supabase.com/docs/guides/auth, https://supabase.com/docs/guides/storage, https://supabase.com/docs/guides/database/postgres/row-level-security (truy cập ngày 29/10/2026).

[7] Paszke, A., Gross, S., Massa, F., Lerer, A., et al. (2019). PyTorch: An Imperative Style, High-Performance Deep Learning Library. In *Advances in Neural Information Processing Systems 32 (NeurIPS 2019)*. https://arxiv.org/abs/1912.01703.

[8] Roboflow. (2023). *Helmet Detection_YOLOv8 – With and Without Helmet Dataset*, phiên bản 3, xuất ngày 10/12/2023. Bộ dữ liệu và thông tin giấy phép/ghi công được mô tả trong [README đi kèm dataset](./datasets/helmet/README.roboflow.txt); giấy phép ghi trong metadata là CC BY 4.0.

> **Ghi chú khi hoàn thiện báo cáo:** Kiểm tra lại ngày truy cập tài liệu theo ngày nộp thực tế; bổ sung URL trang Roboflow Universe chính xác nếu còn truy cập được trang nguồn của phiên bản dataset đang sử dụng. Chỉ bổ sung số liệu đánh giá khi đã có báo cáo metrics của đúng checkpoint và tập kiểm thử.
