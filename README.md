# damda-recipes

**Damda 앱의 파싱 레시피를 호스팅한다.** 앱은 시작할 때 이 저장소의
`v1/recipes.json` 을 HTTPS 로 받아, 번들에 들어 있는 기본 레시피 대신 쓴다.

여기 있는 것은 **데이터뿐이다** — URL 템플릿, HTTP 헤더, 정규식, JSON 경로,
불리언 스위치. 실행 코드는 없고, 앞으로도 없다(아래 *레드라인*).
그래서 이 저장소는 공개돼도 무방하다. 앱 소스 코드는 여기 없다.

- 배포 URL: `https://<사용자명>.github.io/damda-recipes/v1/recipes.json`
- 앱이 받아가는 주기: **앱 시작 시 1회 + 최소 간격 1시간** (`ETag` 조건부 요청)
- 배포 방법: `v1/recipes.json` 을 고쳐서 `main` 에 push. 그게 전부다

---

## 🚨 급할 때 — TikTok / X 가 바뀌어 앱이 고장났다

> 목표는 **스토어 심사 없이 수 분 안에 복구**하는 것이다.
> 아래 순서대로 하면 된다. 이 저장소를 처음 보더라도 상관없다.

### 1단계 — 지혈부터 한다 (2분)

원인을 모르겠다면 **먼저 깨진 소스를 끈다.** 사용자가 실패를 반복하는 것보다,
"지금은 저장할 수 없어요" 안내를 보는 편이 낫다.

`v1/recipes.json` 에서 해당 소스의 `enabled` 를 `false` 로:

```jsonc
"sources": {
  "x": {
    "enabled": false,   // ← 이것만 바꾼다
```

그리고 **`revision` 을 1 올린다**(맨 위에 있다). commit → push. 끝.

- 앱은 그 소스를 **아예 시도하지 않고** 즉시 안내 화면으로 간다
- 다른 소스는 **정상 동작한다**
- 반영: 사용자가 앱을 다시 켜면 즉시. 켜 둔 채인 사용자는 최대 1시간

### 2단계 — 무엇이 깨졌는지 찾는다

레시피로 고칠 수 있는 축은 **넷뿐**이다. 그 밖의 변경(인증 서명 도입 등)은
앱 업데이트 사안이므로 1단계 상태로 두고 앱 저장소로 넘어간다.

| 축 | 필드 | 증상 |
|---|---|---|
| 어디로 요청하나 | `requestUrlTemplate`, `requestHeaders` | 응답이 아예 안 오거나 403/404 |
| 응답 어디를 읽나 | `extract.itemRoots`, `extract.bodyPatterns` | 응답은 오는데 아무것도 못 찾음 |
| 어느 필드가 뭔가 | `fields.*` | 제목·작성자·영상 URL 중 일부만 빔 |
| 실패를 어떻게 읽나 | `extract.statusPaths`, `extract.statusMap` | 엉뚱한 오류 문구가 뜸 |

### 3단계 — 고치고 `revision` 을 올린다

```bash
git clone https://github.com/<사용자명>/damda-recipes.git
cd damda-recipes
$EDITOR v1/recipes.json          # 고친다
                                 # revision +1  ← 잊지 말 것
python3 tools/validate_recipe.py v1/recipes.json --previous <(git show HEAD:v1/recipes.json)
```

**검증기가 통과할 때까지 push 하지 않는다.** 잘못된 레시피는
전 사용자에게 한 번에 나간다 — 단계적 롤아웃이 없다.

**절대 하지 말 것**

- ❌ `schemaVersion` 을 올린다 → 구버전 앱이 레시피를 **통째로 무시**하고 번들
  기본값으로 돌아간다. 그동안 고친 게 전부 사라진다. 앱을 먼저 릴리스해야 한다
- ❌ `hosts` 에 새 호스트를 넣는다 → **소용없다.** 앱 바이너리의 화이트리스트가
  다시 검증한다. 화이트리스트 확장은 앱 업데이트 사안이다
  (`tools/app_host_whitelist.txt` 가 현재 앱이 허용하는 목록의 사본이다)
- ❌ 표현식·스크립트·조건 분기 성격의 필드를 추가한다 → **레드라인.** 아래 참조

### 4단계 — push 하고 확인한다

```bash
git commit -am "fix(x): itemRoots 갱신 — rev 7"
git push
```

- [ ] Actions 탭에서 **레시피 검증**이 초록인지 본다
      (⚠️ Pages 배포는 이 체크를 기다리지 않는다. 빨간불이면 **즉시 되돌린다**)
- [ ] 브라우저로 `https://<사용자명>.github.io/damda-recipes/v1/recipes.json` 을
      열어 실제로 새 내용이 보이는지 본다(캐시 때문에 1~2분 걸릴 수 있다)
- [ ] 실기에서 앱을 **완전히 종료 후 재실행** → 해당 링크로 저장이 되는지 본다
- [ ] 복구됐으면 1단계에서 껐던 `enabled` 를 `true` 로 되돌린다(+ `revision` +1)

### 되돌리기

잘못 나갔으면 되돌리는 것도 push 한 번이다.

```bash
git revert HEAD
# revert 는 revision 도 되돌린다 → 검증기가 "revision 미증가" 로 막는다.
# 값을 직전 최대치보다 크게 손으로 올린 뒤 push 한다.
git push
```

---

## 레드라인 — 이 경계를 넘으면 앱이 스토어에서 삭제된다

Google Play **Device and Network Abuse** 정책은 APK 외부에서 실행 가능한
코드를 받아 앱의 동작을 바꾸는 것을 금지한다. 우리 레시피가 이 조항에
걸리지 않는 이유는 **레시피가 데이터일 뿐**이기 때문이다.

그러므로 다음 성격의 필드를 **추가하지 않는다**:

| 넣지 않는 것 | 예 |
|---|---|
| 스크립트·코드·표현식 | `script`, `code`, `eval`, `expression`, `js` |
| 조건 분기·반복 | `if`, `then`, `else`, `when`, `switch`, `loop`, `while` |
| 함수·콜백 | `fn`, `function`, `lambda`, `callback`, `hook` |
| 실행 지시 | `exec`, `run`, `command`, `shell`, `invoke` |

검증기가 위 낱말이 들어간 키를 **자동으로 거절**한다(`tools/validate_recipe.py`).
"이번만 편하게" 우회하고 싶어지는 순간이 이 게이트가 있는 이유다.
그런 유연성이 정말 필요하다면 그것은 **앱 업데이트로 해결할 문제**다.

YouTube 관련 문자열도 같은 이유로 거절된다. 앱은 YouTube를 전면 배제하며,
원격 경로로 그 문자열이 들어가면 앱 소스가 깨끗해도 소용없다.

---

## 저장소 구조

```
v1/recipes.json                 ← 앱이 받아가는 파일. 실질적으로 이것 하나다
index.html                      ← Pages 루트 안내 페이지
tools/validate_recipe.py        ← 검증기 (의존성 없음, 표준 라이브러리만)
tools/selftest.sh               ← 검증기가 살아 있는지 증명하는 자가진단
tools/app_host_whitelist.txt    ← 앱 바이너리 호스트 화이트리스트의 사본
.github/workflows/validate.yml  ← push 마다 위 둘을 돌린다
```

`v1/` 이라는 경로는 **`schemaVersion` 에 대응**한다. 앱의 `schemaVersion` 이
2로 올라가면 `v2/recipes.json` 을 새로 만들고, `v1/` 은 구버전 앱을 위해
**그대로 남겨 둔다**. 지우면 업데이트하지 않은 사용자의 앱이 원격 갱신을 잃는다.

## 검증기가 보는 것

| # | 검사 | 실패하면 |
|---|---|---|
| 1 | JSON 으로 파싱되는가 | 앱이 응답을 버리고 보존본을 쓴다 |
| 2 | `schemaVersion` ≤ 앱이 아는 값(현재 **1**) | 앱이 레시피를 통째로 무시한다 |
| 3 | `revision` 이 정수이고 직전 커밋보다 증가했는가 | 어느 레시피에서 난 실패인지 진단 불가 |
| 4 | 필수 키 (`sources.x` 의 `hosts`·`extract` 등) | 해당 소스를 해석하지 못한다 |
| 5 | **실행 코드 성격 필드가 없는가** | 스토어 삭제 사유 |
| 6 | **정규식이 실제로 컴파일되는가** (Python + ECMAScript 양쪽) | 파싱이 조용히 전부 실패한다 |
| 7 | **YouTube 문자열 0건** | 정책 위반 |
| 8 | `hosts` 가 앱 화이트리스트 안인가 | 앱이 그 호스트를 요청하지 않는다 |
| 9 | 모든 URL 이 `https` 인가 | 앱이 cleartext 를 거절한다 |
| 10 | 파일이 512KB 이하인가 | 앱이 파싱 전에 버린다 |

정규식은 Python `re` 와 Node `RegExp` 양쪽으로 컴파일해 본다.
앱이 쓰는 Dart `RegExp` 는 ECMAScript 문법이라, Python 만으로는
`(?P<name>...)` 같은 Python 전용 문법을 놓친다.

```bash
python3 tools/validate_recipe.py v1/recipes.json   # 검증
bash tools/selftest.sh                             # 검증기 자체 점검
```

## 앱이 이 파일을 못 받으면 어떻게 되나

**앱은 죽지 않는다.** 폴백 순서가 정해져 있다:

```
원격(이 저장소) → 마지막으로 성공한 원격 레시피(디스크 보존본) → 앱 번들 기본값
```

페치 실패·JSON 오류·`schemaVersion` 초과는 전부 조용히 다음 단계로 넘어간다.
받아 온 레시피를 앱이 쓸 수 없다고 판단하면 **보존본을 덮어쓰지도 않는다** —
쓸 수 있는 레시피가 쓸 수 없는 레시피에 밀려나는 일은 없다.

즉 이 저장소가 잠시 죽어도 앱은 계속 동작한다. 다만 **새 레시피를 내보낼 수
없으므로**, 소스가 깨진 상황에서 이 저장소까지 죽으면 복구 수단이 사라진다.

## 처음 세팅할 때 (한 번만)

1. GitHub 에서 **공개(Public)** 저장소 `damda-recipes` 를 만든다
   - 공개여야 하는 이유: 비공개 저장소의 Pages 는 유료 플랜이 필요하다.
     레시피에는 파싱 경로밖에 없어 공개돼도 잃을 것이 없다
2. 이 디렉터리의 내용을 그대로 push 한다 (`recipe-host/` 안이 저장소 루트다)
3. **Settings → Pages → Source: Deploy from a branch → `main` / `(root)`** 저장
4. 1~2분 뒤 `https://<사용자명>.github.io/damda-recipes/v1/recipes.json` 이
   열리는지 확인한다
5. 그 URL 로 앱을 빌드한다:
   `flutter build appbundle --release --dart-define=RECIPE_URL=<그 URL> ...`
6. 앱 저장소의 호스트 화이트리스트를 **그 단일 호스트로 좁힌다**
   (`app/lib/data/resolver/recipe/recipe_endpoint.dart`)

## 앱 저장소와의 관계

번들 기본 레시피(`app/assets/recipes/default.json`)와 이 파일은 **같은 스키마**다.
둘이 표류하면 신규 설치 사용자와 기존 사용자가 다른 규칙으로 동작한다.
앱 저장소의 `scripts/sync-recipe.sh` 가 차이를 보여주고 동기화한다.

원칙: **여기가 항상 앞선다.** `revision` 은 번들 ≤ 호스팅.
앱을 릴리스할 때 번들을 여기 내용으로 맞춰 올린다.
