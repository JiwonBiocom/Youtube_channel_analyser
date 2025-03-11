def analyze_thumbnails(client, videos_data, is_shorts=False):
    if is_shorts:
        filtered_data = videos_data[videos_data['쇼츠'] == True]
        content_type = "쇼츠(Shorts)"
    else:
        filtered_data = videos_data[videos_data['쇼츠'] == False]
        content_type = "롱폼(Longform)"
    
    # 데이터가 없는 경우
    if filtered_data.empty:
        return f"분석할 {content_type} 영상이 없습니다."
    
    # 모든 동영상을 조회구독비율 기준으로 정렬
    sorted_videos = filtered_data.sort_values(by='조회수/구독자 비율', ascending=False)
    
    # 결과를 저장할 리스트
    all_thumbnail_analyses = []
    
    # 채널 이름 가져오기
    channel_name = filtered_data['채널명'].iloc[0]
    
    # 각 동영상에 대해 분석 수행
    for _, video in sorted_videos.iterrows():
        thumbnail_url = video['썸네일']
        video_id = video['video_id']
        
        prompt = f"""
        YouTube 채널 "{channel_name}"의 "{video['제목']}" 영상의 썸네일 이미지입니다. 그 외 정보는 다음과 같습니다:
        조회수: {video['조회수']}
        좋아요: {video['좋아요']}
        댓글수: {video['댓글수']}
        조회구독비율: {video['조회수/구독자 비율']:.4f}

        이 썸네일을 보고 시각적 요소(색상, 구도, 텍스트, 표정 등)가 이 동영상의 인기를 끄는데 기여했을지 200자 이내로 분석해주세요.
        """
        
        try:
            response = client.chat.completions.create(
                model='gpt-4o-2024-08-06', 
                messages=[
                    {"role": "system", "content": "당신은 유튜브 썸네일 분석 전문가입니다."},
                    {"role": "user", "content": prompt}, 
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": thumbnail_url}}
                    ]}
                ],
                temperature=0.3,
                max_tokens=300
            )
            
            # 분석 결과 저장
            analysis_result = {
                "키워드": video['키워드'],
                "채널명": video['채널명'],
                "video_id": video_id,
                "제목": video['제목'],
                "썸네일": thumbnail_url,
                "분석": response.choices[0].message.content.strip()
            }
            
            all_thumbnail_analyses.append(analysis_result)
            
        except Exception as e:
            analysis_result = {
                "키워드": video['키워드'],
                "채널명": video['채널명'],
                "video_id": video_id,
                "제목": video['제목'],
                "썸네일": thumbnail_url,
                "분석": f"분석 중 오류 발생: {str(e)}"
            }
            all_thumbnail_analyses.append(analysis_result)
    
    return all_thumbnail_analyses


def analyze_channel_video(client, llm, videos_data, is_shorts=False):  # client, llm, videos_data, thumbnail, is_shorts=False  # 썸네일 분석 추가
    # 해당 카테고리(쇼츠/롱폼)에 맞는 영상 필터링
    if is_shorts:
        filtered_data = videos_data[videos_data['쇼츠'] == True]
        content_type = "쇼츠(Shorts)"
    else:
        filtered_data = videos_data[videos_data['쇼츠'] == False]
        content_type = "롱폼(Longform)"
    
    # 데이터가 없는 경우
    if filtered_data.empty:
        return f"분석할 {content_type} 영상이 없습니다."
    
    # 데이터 준비
    data_summary = {
        "영상_수": len(filtered_data),
        "평균_조회수": filtered_data['조회수'].mean(),
        "평균_좋아요": filtered_data['좋아요'].mean(),
        "평균_댓글수": filtered_data['댓글수'].mean(),
        "평균_조회구독비율": filtered_data['조회수/구독자 비율'].mean(),
        "최고_조회수": filtered_data['조회수'].max(),
        "최고_좋아요": filtered_data['좋아요'].max(),
        "최고_댓글수": filtered_data['댓글수'].max()
    }
    
    # 상위 3개 영상 정보 (조회수 기준)
    top_videos = filtered_data.sort_values(by='조회수', ascending=False).head(3)
    top_videos_info = []
    
    for _, row in top_videos.iterrows():
        video_info = {
            "제목": row['제목'],
            "조회수": row['조회수'],
            "좋아요": row['좋아요'],
            "댓글수": row['댓글수'],
            "조회구독비율": row['조회수/구독자 비율'], 
        }
        top_videos_info.append(video_info)
    
    # 프롬프트 작성
    channel_name = filtered_data['채널명'].iloc[0]
    
    prompt = f"""
    다음은 YouTube 채널 "{channel_name}"의 {content_type} 영상 {len(filtered_data)}개에 대한 데이터입니다:
    
    전체 데이터 요약:
    - 영상 수: {data_summary['영상_수']}개
    - 평균 조회수: {data_summary['평균_조회수']:.1f}회
    - 평균 좋아요: {data_summary['평균_좋아요']:.1f}개
    - 평균 댓글 수: {data_summary['평균_댓글수']:.1f}개
    - 평균 조회수/구독자 비율: {data_summary['평균_조회구독비율']:.4f}
    
    상위 3개 영상 (조회수 기준):
    """
    
    for i, video in enumerate(top_videos_info):
        prompt += f"""
    {i+1}. "{video['제목']}"
       - 조회수: {video['조회수']}회
       - 좋아요: {video['좋아요']}개
       - 댓글 수: {video['댓글수']}개
       - 조회수/구독자 비율: {video['조회구독비율']:.4f}
        """
    
    prompt += f"""
    위 데이터를 바탕으로 다음 내용을 분석해주세요:
    1. 조회수, 좋아요, 댓글 수의 전반적인 트렌드와 상관관계
    2. 가장 인기 있는 영상들의 공통점 (제목 패턴, 시청자 참여도 등)
    3. 시청자 참여도가 높은 영상의 특징 (좋아요/조회수 비율, 댓글/조회수 비율 등)
    4. 조회수/구독자 비율을 통해 본 영상의 인기도 분석
    5. {content_type} 영상의 성공 요인과 개선점 제안
    
    분석 결과는 400-500단어 내외로 작성해주세요.
    """
    
    try:
        response = client.chat.completions.create(
            model=llm,  # gpt-4o-2024-08-06
            messages=[
                {"role": "system", "content": "당신은 유튜브 채널과 영상 데이터를 분석하는 전문가입니다. 데이터를 깊이 있게 분석하고 통찰력 있는 인사이트를 제공해주세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        
        # 응답에서 텍스트 추출
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        return f"데이터 분석 중 오류가 발생했습니다: {str(e)}"


def analyze_keyword_video(client, llm, videos_data, is_shorts=False):
    # 해당 카테고리(쇼츠/롱폼)에 맞는 영상 필터링
    if is_shorts:
        filtered_data = videos_data[videos_data['쇼츠'] == True]
        content_type = "쇼츠(Shorts)"
    else:
        filtered_data = videos_data[videos_data['쇼츠'] == False]
        content_type = "롱폼(Longform)"
    
    # 데이터가 없는 경우
    if filtered_data.empty:
        return f"분석할 {content_type} 영상이 없습니다."
    
    # 데이터 준비
    data_summary = {
        "영상_수": len(filtered_data),
        "채널_수": len(filtered_data['채널명'].unique()),
        "평균_조회수": filtered_data['조회수'].mean(),
        "평균_좋아요": filtered_data['좋아요'].mean(),
        "평균_댓글수": filtered_data['댓글수'].mean(),
        "평균_조회구독비율": filtered_data['조회수/구독자 비율'].mean(),
        "최고_조회수": filtered_data['조회수'].max(),
        "최고_좋아요": filtered_data['좋아요'].max(),
        "최고_댓글수": filtered_data['댓글수'].max()
    }
    
    # 상위 3개 영상 정보 (조회수 기준)
    top_videos = filtered_data.sort_values(by='조회수', ascending=False).head(3)
    top_videos_info = []
    
    for _, row in top_videos.iterrows():
        video_info = {
            "제목": row['제목'],
            "채널명": row['채널명'],
            "조회수": row['조회수'],
            "좋아요": row['좋아요'],
            "댓글수": row['댓글수'],
            "조회구독비율": row['조회수/구독자 비율']
        }
        top_videos_info.append(video_info)
    
    popular_channels = filtered_data.groupby('채널명')['조회수'].sum().sort_values(ascending=False).head(3)  # 인기 채널 분석
    keyword = filtered_data['키워드'].iloc[0]  # 키워드 정보 가져오기
    
    prompt = f"""
    다음은 키워드 "{keyword}"의 {content_type} 영상 {len(filtered_data)}개에 대한 데이터입니다:
    
    전체 데이터 요약:
    - 영상 수: {data_summary['영상_수']}개
    - 채널 수: {data_summary['채널_수']}개
    - 평균 조회수: {data_summary['평균_조회수']:.1f}회
    - 평균 좋아요: {data_summary['평균_좋아요']:.1f}개
    - 평균 댓글 수: {data_summary['평균_댓글수']:.1f}개
    - 평균 조회수/구독자 비율: {data_summary['평균_조회구독비율']:.4f}
    
    상위 3개 영상 (조회수 기준):
    """
    
    for i, video in enumerate(top_videos_info):
        prompt += f"""
    {i+1}. "{video['제목']}"
       - 조회수: {video['조회수']}회
       - 좋아요: {video['좋아요']}개
       - 댓글 수: {video['댓글수']}개
       - 조회수/구독자 비율: {video['조회구독비율']:.4f}
        """
    
    # 인기 채널 정보 추가
    prompt += "\n인기 채널 (총 조회수 기준):\n"
    for i, (channel, views) in enumerate(popular_channels.items()):
        prompt += f"{i+1}. {channel}: 총 조회수 {views:,}회\n"
    
    prompt += f"""
    위 데이터를 바탕으로 다음 내용을 분석해주세요:
    1. 키워드 "{keyword}"에 대한 {content_type} 영상의 전반적인 트렌드와 특징
    2. 가장 인기 있는 영상들의 공통점 (제목 패턴, 콘텐츠 유형, 채널 특성 등)
    3. 시청자 참여도가 높은 영상의 특징 (좋아요/조회수 비율, 댓글/조회수 비율 등)
    4. 성공적인 채널들의 전략과 차별점
    5. 이 키워드로 새로운 {content_type} 영상을 제작할 때의 제안점
    
    다양한 채널의 영상을 통합적으로 분석하여 키워드 기반의 인사이트를 제공해주세요.
    분석 결과는 400-500단어 내외로 작성해주세요.
    """
    
    try:
        response = client.chat.completions.create(
            model=llm,  # "gpt-4o-2024-08-06"
            messages=[
                {"role": "system", "content": "당신은 키워드로 묶인 유튜브 영상들의 데이터를 분석하는 전문가입니다. 데이터를 깊이 있게 분석하고 통찰력 있는 인사이트를 제공해주세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        
        # 응답에서 텍스트 추출
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        return f"데이터 분석 중 오류가 발생했습니다: {str(e)}"
