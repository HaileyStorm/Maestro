import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const source = { workspace: 'Synthetic project', name: 'review-clip.mkv', revision: 'r1', type: 'video', artifact_class: 'final', private: false, explicit: false }
const facts = { workspace: source.workspace, name: source.name, revision: source.revision, width: 192, height: 128, frame_count: 124, fps: '24/1', audio_streams: [{ ordinal: 0, label: 'Track 1' }, { ordinal: 1, label: 'Track 2' }] }
// A CPU-created geometric placeholder, never an owner media asset.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAMAAAACACAIAAADS5vE8AAABpUlEQVR4nO3cMWoCURRA0UzIblxVwEYIYlYgrkAIActsNZ2NNuYaPsM/p5xieMXlwYf5s2y2hxf4q9fRA7BuAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZG8jR6gOn68jx4hOX3/jB4hsYFIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiWf33QI/a7z5vH35dzmNftV42EImASAREIiASAZFMdwp74ilptgPXXTYQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCTT3Qu7uvtvjWjCm2I2EMm8G2jCbfEfbCASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIls32MHoGVswGIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiI5Bc5oxQve4/UggAAAABJRU5ErkJggg==', 'base64')
const bundle = build({ stdin: { resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'tsx', contents: `
  import React from 'react';import {createRoot} from 'react-dom/client';
  import {FaceRepairPanel} from './src/components/MainContent/FaceRepairPanel';
  import {hidePrivatePreview,privatePreviewIdentity} from './src/lib/privatePreview';
  const root=createRoot(document.getElementById('root'));let source,current=true;
  const fixture={queueCalls:0,failQueue:false,render(value){source=value;draw()},invalidate(){current=false},hide(){hidePrivatePreview(privatePreviewIdentity(source.workspace,source.name,source.revision))}};
  function draw(){root.render(<FaceRepairPanel source={source} accountScope="synthetic-account" isCurrentSelection={()=>current} onQueued={async check=>{if(!check())return;fixture.queueCalls++;if(fixture.failQueue)throw Error('Private failure')}}/>)}
  window.__faceFixture=fixture;
` }, bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic', logLevel: 'silent' }).then(result=>result.outputFiles[0].text)

async function setup(page: Page, options: { private?: boolean; ambiguous?: boolean; deferFrame?: boolean; deferPost?: boolean; deferProbe?: boolean } = {}) {
  const requests: Array<Record<string, unknown>> = [], frames: number[] = [], unexpected: string[] = []
  let releaseFrame: (() => void) | undefined
  const heldFrame = new Promise<void>(resolve=>{releaseFrame=resolve})
  let releasePost!:()=>void, releaseProbe!:()=>void
  const heldPost=new Promise<void>(resolve=>{releasePost=resolve}),heldProbe=new Promise<void>(resolve=>{releaseProbe=resolve})
  const origin = `http://127.0.0.1:${process.env.MAESTRO_E2E_PORT}`
  await page.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url())
    if(url.origin!==origin){unexpected.push(request.method()+' '+url.pathname);await route.abort();return}
    if(url.pathname==='/api/v1/tools/h3-face-refine/source') {
      expect(Object.fromEntries(url.searchParams)).toEqual({workspace:source.workspace,name:source.name,revision:'r1'})
      if(options.deferProbe)await heldProbe
      await route.fulfill({contentType:'application/json',body:JSON.stringify(facts)});return
    }
    if(url.pathname==='/api/v1/tools/h3-face-refine/frame') {
      expect(url.searchParams.get('revision')).toBe('r1')
      const frame=Number(url.searchParams.get('frame_index'));frames.push(frame)
      if(options.deferFrame && frame===1) await heldFrame
      await route.fulfill({contentType:'image/png',body:PNG});return
    }
    if(url.pathname==='/api/v1/tools/h3-face-refine' && request.method()==='POST') {
      requests.push(request.postDataJSON())
      if(options.deferPost)await heldPost
      await route.fulfill({status:options.ambiguous?503:200,contentType:'application/json',body:JSON.stringify({job_id:'synthetic-face-job',status:'queued'})});return
    }
    if(url.pathname.startsWith('/api/')){unexpected.push(request.method()+' '+url.pathname);await route.abort();return}
    if(url.pathname==='/')await route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synthetic repair editor</title><link rel="stylesheet" href="/src/index.css"></head><body><main id="root"></main></body></html>'})
    else await route.continue()
  })
  await page.goto('/');await page.addScriptTag({content:await bundle})
  await page.evaluate(value=>(window as unknown as {__faceFixture:{render:(value:unknown)=>void}}).__faceFixture.render(value),{...source,private:options.private??false,explicit:true})
  await page.getByRole('button',{name:'Repair face',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'Repair face'})).toBeVisible()
  return {requests,frames,unexpected,releaseFrame:()=>releaseFrame!(),releasePost,releaseProbe}
}
async function review(page:Page){await expect(page.getByRole('img',{name:'Source frame 1',exact:true})).toBeVisible();await page.getByRole('button',{name:'Review this region',exact:true}).click();await page.getByLabel('Describe the face repair').fill('Restore the face detail; preserve expression.')}

test('manual review, shot boundaries and exact source/audio contract',async({page},info)=>{
  const run=await setup(page)
  await review(page)
  await expect(page.getByRole('status').last()).toContainText('1 frames reviewed · 123 unchanged')
  await page.getByLabel('Frame',{exact:true}).fill('63')
  await expect(page.getByRole('img',{name:'Source frame 63',exact:true})).toBeVisible()
  await page.getByLabel('A new shot starts at this frame').check()
  await page.getByLabel('Range start').fill('60');await page.getByLabel('Range end').fill('65')
  await page.getByRole('button',{name:'Review this region for the range',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('within one shot')
  await page.getByLabel('Range start').fill('63');await page.getByLabel('Range end').fill('65')
  await page.getByRole('button',{name:'Review this region for the range',exact:true}).click()
  await page.getByLabel('Source audio guidance').selectOption('1')
  await expect(page.getByRole('status').last()).toContainText('4 frames reviewed · 120 unchanged')
  expect((await new AxeBuilder({page}).include('[role="dialog"]').analyze()).violations).toEqual([])
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.getByRole('dialog').locator('.overflow-y-auto').evaluate(element=>{element.scrollTop=0})
  await page.screenshot({path:info.outputPath('face-repair-overview.png'),fullPage:true,animations:'disabled'})
  if (info.project.name==='desktop-firefox') {
    await page.evaluate(()=>{document.documentElement.style.zoom='2'})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:info.outputPath('face-repair-zoom.png'),fullPage:true,animations:'disabled'})
    await page.evaluate(()=>{document.documentElement.style.zoom=''})
  }
  await page.emulateMedia({reducedMotion:'reduce'})
  expect(await page.getByRole('button',{name:'Close face repair'}).evaluate(element=>getComputedStyle(element).animationName)).toBe('none')
  const targets = await page.getByRole('dialog').locator('button, select, input:not([type="checkbox"]), summary').evaluateAll(elements=>elements.filter(element=>element.getBoundingClientRect().height>0).map(element=>element.getBoundingClientRect().height))
  expect(targets.every(height=>height>=44)).toBe(true)
  await page.getByRole('button',{name:'Queue repaired copy'}).focus();await page.getByRole('button',{name:'Queue repaired copy'}).hover()
  await page.keyboard.press('Shift+Tab');await page.keyboard.press('Tab')
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toBeFocused()
  expect(await page.getByRole('button',{name:'Queue repaired copy'}).evaluate(element=>getComputedStyle(element).outlineStyle)).not.toBe('none')
  await page.screenshot({path:info.outputPath('face-repair-reviewed.png'),fullPage:true,animations:'disabled'})
  await page.getByRole('button',{name:'Queue repaired copy'}).click()
  await expect(page.getByRole('status').last()).toContainText('accepted in Queue')
  expect(run.requests).toHaveLength(1)
  const request=run.requests[0] as {observations:{boxes:unknown[],shots:number[]};frame_multipliers:number[];audio_stream:number;private_output:boolean;explicit_output:boolean}
  expect(request.observations.shots).toEqual([0,62]);expect(request.observations.boxes[1]).toBeNull();expect(request.frame_multipliers[1]).toBe(0)
  expect(request.audio_stream).toBe(1);expect(request.private_output).toBe(false);expect(request.explicit_output).toBe(true)
  expect(run.unexpected).toEqual([])
})
test('hidden previews mount no pixels, frame loading cannot mark the wrong index, keyboard restores focus',async({page},info)=>{
  const run=await setup(page,{private:true,deferFrame:true})
  await expect(page.getByRole('button',{name:'Reveal source preview'})).toBeVisible();expect(run.frames).toEqual([])
  await page.getByRole('button',{name:'Reveal source preview'}).click();await review(page)
  await page.getByRole('button',{name:'Next',exact:true}).click()
  await expect(page.getByRole('button',{name:'Review this region',exact:true})).toBeDisabled()
  await expect(page.getByText('Loading source frame…')).toBeVisible()
  run.releaseFrame();await expect(page.getByRole('img',{name:'Source frame 2',exact:true})).toBeVisible()
  await expect(page.getByText('of 124 · Unchanged')).toBeVisible()
  await page.evaluate(()=>(window as unknown as {__faceFixture:{hide:()=>void}}).__faceFixture.hide())
  await expect(page.getByRole('img')).toHaveCount(0)
  await page.screenshot({path:info.outputPath('face-repair-hidden.png'),fullPage:true})
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Repair face',exact:true})).toBeFocused()
  expect(run.requests).toEqual([]);expect(run.unexpected).toEqual([])
})
test('uncertain acknowledgement and failed Queue refresh never resend a repair',async({page},info)=>{
  const run=await setup(page,{ambiguous:true});await review(page)
  await page.getByRole('button',{name:'Queue repaired copy'}).evaluate(button=>{button.dispatchEvent(new MouseEvent('click',{bubbles:true}));button.dispatchEvent(new MouseEvent('click',{bubbles:true}))})
  await expect(page.getByRole('alert')).toContainText('acknowledgement is unavailable')
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toHaveCount(0)
  await page.evaluate(()=>(window as unknown as {__faceFixture:{failQueue:boolean}}).__faceFixture.failQueue=true)
  await page.getByRole('button',{name:'Open Queue'}).click();await expect(page.getByRole('alert')).toContainText('Queue refresh failed')
  await page.screenshot({path:info.outputPath('face-repair-error.png'),fullPage:true})
  await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'Repair face',exact:true}).click()
  await expect(page.getByRole('status').last()).toContainText('earlier submission needs review')
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toHaveCount(0)
  await page.reload();await page.addScriptTag({content:await bundle})
  await page.evaluate(value=>(window as unknown as {__faceFixture:{render:(value:unknown)=>void}}).__faceFixture.render(value),source)
  await page.getByRole('button',{name:'Repair face',exact:true}).click()
  await expect(page.getByRole('status').last()).toContainText('earlier submission needs review')
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toHaveCount(0)
  expect(run.requests).toHaveLength(1);expect(run.unexpected).toEqual([])
})
test('scaled noncentral mouse or touch drag submits the exact visible square',async({page},info)=>{
  const run=await setup(page)
  const image=page.getByRole('img',{name:'Source frame 1',exact:true});await expect(image).toBeVisible()
  const bounds=(await image.boundingBox())!
  const from={x:bounds.x+bounds.width*.2,y:bounds.y+bounds.height*.3},to={x:bounds.x+bounds.width*.6,y:bounds.y+bounds.height*.8}
  if(info.project.name==='android-like-chromium') {
    const touch=await page.context().newCDPSession(page)
    await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:from.x,y:from.y}]})
    await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:to.x,y:to.y}]})
    await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.detach()
  } else {await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(to.x,to.y,{steps:5});await page.mouse.up()}
  const x=Math.round(192*.2),y=Math.round(128*.3),side=Math.min(Math.round(192*.6)-x,Math.round(128*.8)-y)
  await expect(page.getByLabel('Left',{exact:true})).toHaveValue(String(x));await expect(page.getByLabel('Top',{exact:true})).toHaveValue(String(y))
  await expect(page.getByLabel('Size',{exact:true})).toHaveValue(String(side))
  await page.getByRole('button',{name:'Review this region',exact:true}).click();await page.getByLabel('Describe the face repair').fill('Preserve the face expression.')
  await page.getByRole('button',{name:'Queue repaired copy'}).click()
  await expect(page.getByRole('status').last()).toContainText('accepted in Queue')
  expect((run.requests[0] as {observations:{boxes:unknown[]}}).observations.boxes[0]).toEqual([x,y,x+side,y+side])
  expect(run.requests).toHaveLength(1);expect(run.unexpected).toEqual([])
})
test('invalidated project selection cannot submit the reviewed source',async({page})=>{
  const run=await setup(page);await review(page)
  await page.evaluate(()=>(window as unknown as {__faceFixture:{invalidate:()=>void}}).__faceFixture.invalidate())
  await page.getByRole('button',{name:'Queue repaired copy'}).click()
  expect(run.requests).toEqual([]);expect(run.unexpected).toEqual([])
})
test('pending receipt survives closing and settles without replay; empty review stays disabled',async({page},info)=>{
  const run=await setup(page,{deferPost:true,deferProbe:true})
  await expect(page.getByText('Reading source frames…')).toBeVisible()
  await page.screenshot({path:info.outputPath('face-repair-loading.png'),fullPage:true})
  run.releaseProbe();await expect(page.getByRole('img',{name:'Source frame 1',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toBeDisabled()
  await page.screenshot({path:info.outputPath('face-repair-empty.png'),fullPage:true})
  await review(page);await page.getByRole('button',{name:'Queue repaired copy'}).click()
  await expect.poll(()=>run.requests.length).toBe(1)
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'Repair face',exact:true}).click()
  await expect(page.getByRole('status').last()).toContainText('Submitting repair')
  await expect(page.getByRole('button',{name:'Queue repaired copy'})).toBeDisabled()
  run.releasePost();await expect(page.getByRole('status').last()).toContainText('accepted in Queue')
  await page.getByRole('button',{name:'Start another repair'}).click()
  await expect(page.getByRole('status').last()).toContainText('0 frames reviewed · 124 unchanged')
  expect(run.requests).toHaveLength(1);expect(run.unexpected).toEqual([])
})
