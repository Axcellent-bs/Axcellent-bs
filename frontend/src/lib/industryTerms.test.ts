// node --test로 돈다 (npm test). 업종 선택지와 업종별 낱말 (서버 ax_agent_template.industry_terms와 같은 표)
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DEFAULT_TERMS, INDUSTRIES, termsFor } from './industryTerms.ts'

test('선택지는 겹치지 않고 마지막이 기타', () => {
  assert.equal(INDUSTRIES.at(-1), '기타')
  assert.equal(new Set(INDUSTRIES).size, INDUSTRIES.length)
  for (const name of ['여행사', '교육', '서점·출판', '온라인 쇼핑몰']) assert.ok(INDUSTRIES.includes(name), name)
})

test('업종에 맞는 낱말: 여행사는 예약, 교육은 수강 신청, 서점은 책', () => {
  assert.deepEqual(termsFor('여행사'), { item: '여행 상품', order: '예약' })
  assert.deepEqual(termsFor('교육'), { item: '강의', order: '수강 신청' })
  assert.equal(termsFor('서점·출판').item, '책')
})

test('자유롭게 적은 업종도 낱말이 들어 있으면 맞춘다', () => {
  assert.equal(termsFor('온라인  여행 예약 플랫폼').order, '예약')
  assert.equal(termsFor('에듀테크').item, '강의')
})

test('모르는 업종·빈 값은 지어내지 않고 기본 낱말', () => {
  for (const x of ['', null, undefined, '기타', '온라인 쇼핑몰', '우주 항만']) assert.deepEqual(termsFor(x), DEFAULT_TERMS)
})
