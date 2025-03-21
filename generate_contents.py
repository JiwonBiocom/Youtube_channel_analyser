def generate_from_channel(client, keyword, info, llm):
    prompt = f""""""

    response = client.chat.completions.create(
        model=llm, 
        messages=[
            {'role': 'system', 'content': f"당신은 카피라이팅 법칙을 따라 {keyword} 주제의 유튜브 동영상 컨텐츠를 만드는 전문가입니다."}, 
            {'role': 'user', 'content': prompt}, 
        ], 
        max_tokens=1500, 
        temperature=0.3, 
    )

    return response.choices[0].message.content.strip()


def generate_from_keyword(client, keyword, info, llm):
    prompt = f"""새로 만들 유튜브 동영상을 위한 제목, 썸네일 이미지 내용, 첫 2분 스크립트 내용을 생성해야 합니다.

    제목 및 썸네일 이미지 내용은 다음 카피라이팅 법칙 5가지를 꼭 지켜서 생성해주세요:
    1. NUMBERS (구체적 숫자 / 전후 비교)
    2. ONE & ONLY (하나만 지키면 된다 / 단 하나의 비밀)
    3. SHOCK & HOOK (충격·호기심 + 짧고 강렬한 표현)
    4. AUTHORITY (전문가·유명인 인용, 권위 부여)
    5. URGENCY (시급성·즉시성)

    첫 2분 스크립트는 다음 글쓰기 법칙 4가지를 꼭 지켜서 작성해주세요:
    1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
    2. HOOK & FLOW (후킹 → 자연스러운 흐름)
    3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
    4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

    참고할 동영상 정보:
    {info}

    위 정보들을 토대로 '{keyword}'를 주제로 하고, pdf 파일 내용에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 아래 포맷대로로 생성해주세요:
    [제목]
    제목 3가지

    [썸네일]
    썸네일 3가지

    [스크립트]
    첫 2분 스크립트 내용
"""

    response = client.chat.completions.create(
        model=llm, 
        messages=[
            {'role': 'system', 'content': "당신은 카피라이팅 법칙을 따라 유튜브 동영상 컨텐츠를 만드는 전문가입니다."}, 
            {'role': 'user', 'content': prompt}, 
        ], 
        max_tokens=1500, 
        temperature=0.3, 
    )

    return response.choices[0].message.content.strip()
