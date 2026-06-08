# 개발자 포트폴리오 웹사이트 (GitHub Pages 호스팅용)

이 프로젝트는 개인 개발자 포트폴리오를 작성하고 깃허브 페이지(GitHub Pages)를 이용해 즉시 배포할 수 있는 템플릿입니다. 어두운 배경의 모던 글래스모피즘 테마를 갖추고 있으며, 스크롤 애니메이션 및 테마 토글(다크/라이트) 기능이 기본 제공됩니다.

특히 본 저장소의 하위 또는 인근 폴더에 설정된 `google-drive-integration` 프로젝트로 이동하는 데모 링크가 설정되어 있어, 실제 작동하는 토이 프로젝트를 포트폴리오 상에서 보여줄 수 있도록 구성되었습니다.

---

## ✨ 주요 특징
* **모던 다크/라이트 테마**: CSS 변수(Custom Properties)를 사용해 지연 없는 부드러운 테마 전환 제공
* **인터랙티브 마우스 라이트**: 마우스 커서를 따라 부드러운 하이라이트가 이동하여 입체감 향상
* **반응형 웹 디자인**: 모바일, 태블릿, 데스크톱 화면 비율에 맞춰 자동 최적화되는 그리드 레이아웃
* **스크롤 애니메이션**: `Intersection Observer` API 기반의 부드러운 스크롤 페이드인(Scroll Reveal) 적용
* **이메일 원클릭 복사**: 편리한 클립보드 복사 유틸 탑재

---

## 🚀 로컬 실행 방법

1. 터미널을 열고 포트폴리오 디렉토리로 이동한 뒤, 파이썬 정적 웹 서버를 구동합니다.
   ```bash
   cd /home/soli/.gemini/antigravity/scratch/github-portfolio
   python3 -m http.server 8000
   ```
2. 웹 브라우저를 열고 **`http://localhost:8000`**으로 접속합니다.

---

## 🌐 깃허브 페이지(GitHub Pages) 배포 방법

이 프로젝트는 완전히 정적(Static) 파일로만 구성되어 있어 **무료로 깃허브 웹 서버에 배포**할 수 있습니다.

### Step 1: 깃허브 레포지토리 생성
1. [GitHub](https://github.com/)에 로그인합니다.
2. 새 저장소(New Repository)를 생성합니다. (예: `my-portfolio`)
3. 저장소의 상태를 **Public**으로 설정합니다. (Pages 기능은 퍼블릭 저장소에서 무료입니다.)

### Step 2: 코드 업로드하기
로컬 폴더에서 Git을 초기화하고 작성한 파일들을 커밋한 후 깃허브로 업로드합니다.
```bash
# git 초기화
git init

# 파일 추가 및 커밋
git add .
git commit -m "Initialize portfolio website"

# 원격 저장소 연결 (username과 repo-name은 본인 정보로 교체)
git branch -M main
git remote add origin https://github.com/your-username/my-portfolio.git

# 코드 푸시
git push -u origin main
```

### Step 3: GitHub Pages 활성화
1. 생성한 GitHub 레포지토리의 **[Settings]** 탭으로 이동합니다.
2. 왼쪽 사이드바에서 **[Pages]** 메뉴를 클릭합니다.
3. Build and deployment의 Source 항목이 **"Deploy from a branch"**로 되어 있는지 확인합니다.
4. Branch 항목에서 **`main`** 브랜치와 **`/ (root)`** 폴더를 선택한 뒤 **[Save]** 버튼을 누릅니다.
5. 약 1~2분 뒤 페이지가 새로고침되면 상단에 배포 완료 링크가 생성됩니다. (예: `https://your-username.github.io/my-portfolio/`)

---

## 🛠️ 포트폴리오 커스텀 팁
- `index.html` 파일 내부의 텍스트 및 이메일 주소(`your-email@example.com`), 깃허브 주소를 본인의 이력에 맞게 변경하여 사용하십시오.
- `app.js`에서 추가 프로젝트 카드의 이미지 경로 및 라이브 데모 URL을 실제 주소로 대체하여 활용하시면 좋습니다.
