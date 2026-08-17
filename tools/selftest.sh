#!/usr/bin/env bash
# 검증기 자체가 살아 있는지 증명한다 (docs/09 §8.1 의 "양성 대조군" 과 같은 발상).
#
# 통과하는 레시피만 확인하면 "검사기가 아무것도 안 하고 있는" 상태와 구분되지 않는다.
# 그래서 일부러 망가뜨린 레시피들이 **각각 기대한 사유로** 떨어지는지 본다.
#
# 사용법: tools/selftest.sh [v1/recipes.json]
set -uo pipefail

# 인자는 **호출한 디렉터리 기준**으로 받고, 그 다음에 저장소 루트로 옮긴다.
# (CI 는 앱 저장소 루트에서 recipe-host/v1/recipes.json 처럼 넘긴다)
BASE=${1:-v1/recipes.json}
case "$BASE" in /*) ;; *) [ -f "$BASE" ] && BASE="$PWD/$BASE" ;; esac
cd "$(dirname "$0")/.." || exit 1
VALIDATE="python3 tools/validate_recipe.py"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0

# mutate <이름> <기대 사유 조각> <python 변형식>
#   변형식은 dict `d` 를 제자리에서 고친다. 문자열 그대로를 바꿔야 하면
#   raw_* 변형은 아래 mutate_raw 를 쓴다.
mutate() {
  local name=$1 expect=$2 code=$3
  local out="$WORK/mutant.json"
  python3 - "$BASE" "$out" <<PY
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
d['revision'] = d['revision'] + 1   # 변형과 무관하게 revision 검사는 통과시킨다
$code
with open(sys.argv[2], 'w', encoding='utf-8') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
PY
  assert_fails "$name" "$expect" "$out"
}

mutate_raw() {
  local name=$1 expect=$2 sed_expr=$3
  local out="$WORK/mutant.json"
  sed "$sed_expr" "$BASE" > "$out"
  assert_fails "$name" "$expect" "$out"
}

assert_fails() {
  local name=$1 expect=$2 file=$3
  local output
  output=$($VALIDATE "$file" --previous "$BASE" 2>&1)
  if [ $? -eq 0 ]; then
    echo "  ✗ $name — 검증기가 통과시켰다. 게이트가 고장났다."
    echo "$output" | sed 's/^/      /'
    fail=$((fail + 1))
    return
  fi
  if ! echo "$output" | grep -q "FAIL  $expect"; then
    echo "  ✗ $name — 떨어지긴 했으나 사유가 '$expect' 가 아니다."
    echo "$output" | grep '^  FAIL' | sed 's/^/      /'
    fail=$((fail + 1))
    return
  fi
  echo "  ✓ $name → $expect"
  pass=$((pass + 1))
}

echo "정상 레시피"
if $VALIDATE "$BASE" > "$WORK/good.log" 2>&1; then
  echo "  ✓ $BASE 통과"
  pass=$((pass + 1))
else
  echo "  ✗ $BASE 가 떨어진다. 배포할 수 없는 상태다."
  sed 's/^/      /' "$WORK/good.log"
  fail=$((fail + 1))
fi

echo
echo "망가뜨린 레시피 (각각 떨어져야 한다)"

mutate_raw "JSON 문법 깨짐" "JSON 파싱" 's/"schemaVersion": 1,/"schemaVersion": 1,,/'

mutate "schemaVersion 초과" "schemaVersion" \
  "d['schemaVersion'] = 2"

# 내용이 바뀌었는데 revision 을 안 올린 경우가 실제 위험이다.
# (내용이 그대로면 올릴 이유가 없고, 같은 내용에 다른 번호를 붙이면 진단이 헷갈린다.)
mutate "revision 미증가 (내용은 바뀜)" "revision" \
  "d['revision'] = d['revision'] - 1; d['sources']['x']['hosts'].append('m.x.com')"

mutate "revision 정수 아님" "revision" \
  "d['revision'] = '5'"

mutate "필수 키 누락 (x.hosts)" "필수 키" \
  "del d['sources']['x']['hosts']"

mutate "필수 키 누락 (x.extract)" "필수 키" \
  "del d['sources']['x']['extract']"

mutate "실행 코드 필드 (script)" "실행 코드 성격 필드" \
  "d['sources']['x']['script'] = 'return 1'"

mutate "실행 코드 필드 (onError.eval)" "실행 코드 성격 필드" \
  "d['sources']['x']['onError'] = {'eval': 'x'}"

mutate "실행 코드 필드 (조건 분기)" "실행 코드 성격 필드" \
  "d['sources']['x']['fields']['title'] = {'if': 'a', 'then': 'b'}"

mutate "실행 코드 값 (화살표 함수)" "실행 코드 성격 필드" \
  "d['sources']['x']['canonicalTemplate'] = 'x => x.id'"

mutate "정규식 깨짐 (괄호 불일치)" "정규식 컴파일" \
  "d['sources']['x']['postIdPattern'] = '/(?:status/(\\\\d+)'"

mutate "정규식 깨짐 (닫히지 않은 클래스)" "정규식 컴파일" \
  "d['sources']['x']['extract']['patterns']['variantSize'] = '/(\\d+x(\\d+)/'"

mutate "정규식 — 파이썬은 되나 Dart 는 안 되는 문법" "정규식 컴파일 (ECMAScript)" \
  "d['sources']['x']['postIdPattern'] = '/status/(?P<id>\\\\d+)'"

mutate "YouTube 문자열 (hosts)" "YouTube 게이트" \
  "d['sources']['x']['hosts'].append('youtube.com')"

mutate "YouTube 문자열 (헤더 값)" "YouTube 게이트" \
  "d['sources']['x']['requestHeaders']['Referer'] = 'https://youtu.be/'"

mutate "화이트리스트 밖 호스트" "호스트 화이트리스트" \
  "d['sources']['x']['hosts'].append('evil.example.com')"

mutate "화이트리스트 밖 단축 링크 호스트" "호스트 화이트리스트" \
  "d['sources']['x']['shortLinkHosts'].append('bit.ly')"

mutate "cleartext http URL" "URL 스킴" \
  "d['sources']['x']['requestUrlTemplate'] = 'http://cdn.syndication.twimg.com/x'"

echo
echo "통과 $pass · 실패 $fail"
[ "$fail" -eq 0 ] || exit 1
echo "검증기는 정상 동작한다."
