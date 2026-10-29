const test = require('node:test');
const assert = require('node:assert/strict');
const {selected} = require('../src/stellis/static/health-dashboard.js');
test('selections retain explicit order and omit unavailable notebooks',()=>{
 assert.deepEqual(selected([{id:'b'},{id:'a'}],['a','deleted','b']),[{id:'a'},{id:'b'}]);
 assert.deepEqual(selected([{id:'a'}],[]),[]);
});
