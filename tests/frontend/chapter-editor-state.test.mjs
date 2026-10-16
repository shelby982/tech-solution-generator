import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chapterDisplayState,localDraftAction} from '../../frontend/assets/chapter-studio.mjs';

test('编辑、协作块和预览始终只展示一种正文视图',()=>{
  for(const preview of [false,true])for(const live of [false,true]){
    const display=chapterDisplayState(preview,live);
    assert.equal(Object.values(display).filter(hidden=>!hidden).length,1);
    assert.equal(display.previewHidden,!preview);
  }
});

test('普通草稿自动恢复，只有服务器正文变化才要求处理冲突',()=>{
  assert.equal(localDraftAction(null,'正文'),'none');
  assert.equal(localDraftAction({content:'正文',base:'旧版本'},'正文'),'none');
  assert.equal(localDraftAction({content:'新编辑',base:'正文'},'正文'),'restore');
  assert.equal(localDraftAction({content:'新编辑',base:'旧版本'},'服务器新版本'),'conflict');
});
