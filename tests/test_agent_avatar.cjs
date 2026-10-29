const test = require('node:test');
const assert = require('node:assert/strict');
const {spritePath, colorFor, isCoordinator, AVATAR_COLORS} = require('../src/stellis/shared/agent-avatar.js');

const cells = path => [...path.matchAll(/M(\d) (\d)h1v1h-1z/g)].map(match => `${match[1]},${match[2]}`);

test('a seed always draws the same sprite and colour', () => {
  assert.equal(spritePath('agent-1'), spritePath('agent-1'));
  assert.equal(colorFor('agent-1'), colorFor('agent-1'));
  assert.ok(AVATAR_COLORS.includes(colorFor('agent-1')));
});

test('sprites are mirrored about the middle column and keep both eyes open', () => {
  for (const seed of ['a', 'b', 'general', '3f2c9e1a-8d4b-4c51-9a0e-1b2c3d4e5f60']) {
    const set = new Set(cells(spritePath(seed)));
    for (const key of set) { const [x, y] = key.split(',').map(Number); assert.ok(set.has(`${6 - x},${y}`), `${seed} ${key}`); }
    assert.ok(!set.has('2,3') && !set.has('4,3'), seed);
  }
});

test('different seeds draw different sprites', () => {
  const paths = new Set(Array.from({length: 40}, (_, index) => spritePath(`agent-${index}`)));
  assert.ok(paths.size > 8);
});

test('the default agent and stewards coordinate; other agents act', () => {
  assert.equal(isCoordinator({is_default: true, notebooks: []}), true);
  assert.equal(isCoordinator({is_default: false, notebooks: [{is_steward: true}]}), true);
  assert.equal(isCoordinator({is_default: false, notebooks: [{is_steward: false}]}), false);
  assert.equal(isCoordinator(null), false);
});
