import PyPDF2


def extract_text_from_pdf(pdf_file):
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    
    for page_num in range(len(pdf_reader.pages)):
        page = pdf_reader.pages[page_num]
        text += page.extract_text()
    
    return text

def generate_from_pdf2youtube(client, pdf_text, keyword, llm):
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

    PDF 내용:
    {pdf_text[:3000]}

    위 정보들을 토대로 '{keyword}'를 주제로 하고, pdf 파일 내용에 관한 동영상 제목 및 썸네일 이미지 내용 각각 3가지, 스크립트 하나를 생성해주세요.

    꼭 아래 포맷대로 생성 해주세요:
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
            {'role': 'system', 'content': "당신은 카피라이팅 법칙을 따라 pdf 내용에 기반하여 유튜브 동영상 컨텐츠를 만드는 전문가입니다."}, 
            {'role': 'user', 'content': prompt}
        ], 
        max_tokens=1500, 
        temperature=0.3, 
    )

    return response.choices[0].message.content.strip()


def generate_from_pdf2instagram(client, pdf_text, keyword, llm):
    prompt = f"""Instagram 콘텐츠를 작성하고자 합니다.

        인스타그램은 사진과 영상이 핵심인 시각적 플랫폼이지만, 게시글(캡션)과 해시태그 역시 매우 중요합니다. 아래 핵심 요소들을 게시글에 반영해 주세요:

        1. 비주얼 중심의 디자인:
        - 고퀄리티 이미지/영상: 인스타그램은 시각적 플랫폼이므로, 선명하고 매력적인 사진이나 영상을 사용해야 합니다.
        - 통일된 스타일: 필터, 색감, 레이아웃 등에서 일관성을 유지해 브랜드 정체성을 확실히 하세요.

        2. 간결하고 강렬한 캡션:
        - 핵심 메시지 전달: 짧은 문장 안에 핵심 아이디어나 스토리를 담아 독자의 관심을 유도합니다.
        - 후킹 요소 포함: 캡션의 첫 문장으로 질문, 도발적인 문구, 혹은 놀라운 사실을 제시해 관심을 끌어보세요.

        3. 효과적인 해시태그 전략:
        - 관련 해시태그 활용: 콘텐츠와 연관된 인기 해시태그를 선택해 도달 범위를 확장합니다.
        - 브랜딩 해시태그: 고유의 브랜드 해시태그를 만들어 꾸준히 사용하면, 팔로워들이 쉽게 관련 콘텐츠를 찾아볼 수 있습니다.

        4. 대화형 및 참여 유도
        - 콜 투 액션 (CTA): 팔로워에게 댓글을 달거나 질문에 응답하도록 유도하는 문구를 포함하세요.
        - 스토리, 리포스트, DM 활용: 인스타그램의 다양한 기능을 활용해 사용자와의 상호작용을 극대화합니다.

        5. 일관된 톤과 메시지:
        - 브랜드 보이스 유지: 작성하는 모든 캡션과 콘텐츠에서 브랜드의 톤과 메시지를 일관되게 전달해 신뢰도를 높입니다.

        그리고 아래 글쓰기 법칙 4가지를 지켜주세요:
        1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
        2. HOOK & FLOW (후킹 → 자연스러운 흐름)
        3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
        4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

    PDF 내용:
    {pdf_text[:3000]}

    위 정보들을 토대로 '{keyword}'를 주제로 하고, pdf 파일 내용에 관한 인스타그램 게시물을 아래 포맷에 맞춰 생성해 주세요:
    [사진]

    [게시글]

    [해시 태그]
"""

    response = client.chat.completions.create(
        model=llm, 
        messages=[
            {'role': 'system', 'content': "당신은 카피라이팅 법칙을 따라 pdf 내용에 기반하여 인스타그램 콘텐츠를 만드는 전문가입니다."}, 
            {'role': 'user', 'content': prompt}
        ], 
        max_tokens=1500, 
        temperature=0.3, 
    )

    return response.choices[0].message.content.strip()


def generate_from_pdf2threads(client, pdf_text, keyword, llm):
    prompt = f"""Threads 콘텐츠를 작성하고자 합니다.

    게시글에는 다음과 같은 핵심 요소가 반드시 포함되어야 합니다:
    1. 핵심 메시지와 목적 명확화: 짧은 글로 강렬한 핵심 메시지를 전달하기 위해, 콘텐츠의 주제와 목표를 사전에 명확히 설정할 것
    2. 후킹(Hook) 요소: 첫 문장은 독자의 관심을 즉시 끌어야 하므로, 질문, 도발적인 주장 또는 놀라운 사실 등으로 시작하여 관심을 유도할 것
    3. 간결함과 명료함: 제한된 글자수 내에서 불필요한 단어를 제거하고 핵심 메시지만 담도록 작성할 것
    4. 비주얼 요소의 활용: 이미지나 이모지 등 시각적 요소를 적절히 사용하여 눈길을 끌고 메시지를 보완할 것
    5. 일관된 톤과 브랜딩: 브랜드나 개인의 목소리를 일관되게 유지해 팔로워들이 콘텐츠를 쉽게 인식할 수 있도록 할 것
    6. 대화형 접근: 소통과 댓글을 통한 상호작용이 중요하므로, 독자에게 질문을 던지거나 의견을 유도하는 콜 투 액션(CTA)을 포함할 것

    아래 글쓰기 법칙 4가지를 지켜주세요:
    1. SHORT & SIMPLE (짧고 간결하게, 핵심 먼저)
    2. HOOK & FLOW (후킹 → 자연스러운 흐름)
    3. YOU-FOCUSED (독자 중심, 독자의 이익 강조)
    4. CREDIBILITY & ACTION (신뢰도 확보 + 행동 유도)

    PDF 내용:
    {pdf_text[:3000]}

    위 정보들을 토대로 '{keyword}'를 주제로 하고, pdf 파일 내용에 관한 Threads 게시물을 아래 포맷에 맞춰 생성해주세요:
    [게시글]

    [사진]

    [태그]
"""

    response = client.chat.completions.create(
        model=llm, 
        messages=[
            {'role': 'system', 'content': "당신은 카피라이팅 법칙을 따라 pdf 내용에 기반하여 스레드 컨텐츠를 만드는 전문가입니다."}, 
            {'role': 'user', 'content': prompt}
        ], 
        max_tokens=1500, 
        temperature=0.3, 
    )

    return response.choices[0].message.content.strip()
