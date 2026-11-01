"""지역별 Google OAuth 인증으로 유튜브에 영상을 올린다."""
import pickle
from datetime import timezone


def upload_video(credentials_path, video_path, plan, publish_at, immediate):
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    with credentials_path.open("rb") as file:
        credentials = pickle.load(file)
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise ValueError(f"유효하지 않은 유튜브 인증 파일: {credentials_path}")
    title, description, tags = (plan.get(key) for key in ("유튜브_제목", "유튜브_본문", "유튜브_태그"))
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("기획에 유효한 유튜브 제목, 본문, 태그가 필요합니다")
    status = {"privacyStatus": "public" if immediate else "private"}
    if not immediate:
        status["publishAt"] = publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"snippet": {"title": title, "description": description, "tags": tags}, "status": status}
    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    request = service.videos().insert(part="snippet,status", body=body,
                                      media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]
