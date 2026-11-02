"""주소의 광역 지역과 사용자가 지정한 채널을 연결한다."""

from itertools import chain


REGION_CHANNELS = {
    "인천": "인천", "인천광역시": "인천",
    "서울": "서울", "서울특별시": "서울",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충청": "충청", "충북": "충청", "충남": "충청", "충청북도": "충청", "충청남도": "충청",
    "대전": "대전", "대전광역시": "대전",
    "전라": "전라", "전북": "전라", "전남": "전라", "전라북도": "전라", "전북특별자치도": "전라", "전라남도": "전라", "전남광주통합특별시": "전라",
    "경상": "경상", "경북": "경상", "경남": "경상", "경상북도": "경상", "경상남도": "경상",
    "대구": "경상", "대구광역시": "경상", "울산": "경상", "울산광역시": "경상",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주",
    "광주": "전라", "광주광역시": "전라",
    "부산": "부산", "부산광역시": "부산",
}

CHANNEL_ORDER = ("인천", "서울", "경기", "강원", "충청", "대전", "전라", "경상", "제주", "부산")


def channel_for_address(address):
    """시·도 명칭만 비교한다. 대응이 없는 지역은 추측하지 않는다."""
    parts = (address or "").strip().split()
    return REGION_CHANNELS.get(parts[0]) if parts else None


def iter_region_place_iterators(db, after_id=""):
    """각 지역의 장소 반복자를 순환한다. 건너뛴 가게는 해당 지역에서 계속 읽는다."""
    address = "COALESCE(NULLIF(TRIM(address_name), ''), TRIM(road_address_name), '')"
    region = f"substr({address}, 1, instr({address} || ' ', ' ') - 1)"
    cursors = []
    for channel in CHANNEL_ORDER:
        aliases = [name for name, target in REGION_CHANNELS.items() if target == channel]
        placeholders = ",".join("?" for _ in aliases)
        sql = f"SELECT * FROM places WHERE place_id > ? AND {region} IN ({placeholders}) ORDER BY place_id"
        cursors.append(iter(db.execute(sql, (after_id, *aliases))))
    while cursors:
        active = []
        for cursor in cursors:
            row = next(cursor, None)
            if row is not None:
                active.append(cursor)
                yield chain((row,), cursor)
        cursors = active
