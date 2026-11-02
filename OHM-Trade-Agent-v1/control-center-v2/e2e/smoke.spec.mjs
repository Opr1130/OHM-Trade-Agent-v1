import {test,expect} from '@playwright/test';
const hash='a'.repeat(64);
test('dashboard shows real evidence classification and protects admin flow',async({page})=>{
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());const path=url.pathname;
  if(route.request().headers()['x-webhook-secret']!=='test-viewer')return route.fulfill({status:401,json:{detail:'Invalid dashboard secret'}});
  let body={};
  if(path==='/api/analytics/summary')body={scan_runs:4,qualified_signals:0};
  else if(path==='/api/analytics/intelligence')body={status:'INSUFFICIENT_DATA',freshness:{status:'UNKNOWN'}};
  else if(path==='/api/opip/zero-trade-explanation')body={status:'NO_QUALIFIED_SIGNAL',reason:'No qualified signals recorded'};
  else if(path==='/api/opip/scoring-comparison')body={status:'RECORDED',rows:[{scan_id:'SCAN:fixture',observed_at_utc:'2026-11-02T18:00:00Z',model_authority:'V2_PRODUCTION_V1_OBSERVATION',v1_top:[],v2_top:[]}]};
  else if(path==='/api/opip/strategy-admin/draft')body={status:'REFERENCE_ONLY',digest:hash,document:{scoring:{v2_threshold:75},advisory_economics:{},ai_advisory:{},protection:{}}};
  else if(path==='/api/opip/strategy-admin/active')body={status:'BUNDLED_DEFAULT',digest:hash,document:{scoring:{v2_threshold:75},advisory_economics:{},ai_advisory:{},protection:{}}};
  else return route.fulfill({status:404,json:{detail:'Unavailable in fixture'}});
  return route.fulfill({json:body});
 });
 await page.goto('/dashboard/v2/');
 await expect(page.getByRole('heading',{name:'Overview'})).toBeVisible();
 await expect(page.getByText('AWAITING EVIDENCE')).toBeVisible();
 await page.getByPlaceholder('X-Webhook-Secret (session memory only)').fill('test-viewer');
 await page.getByRole('button',{name:'Refresh'}).click();
 await expect(page.getByText('SCAN:fixture')).toBeVisible();
 await expect(page.getByText('V2 production · V1 observation')).toBeVisible();
 await page.getByRole('button',{name:'V1 / V2 Evidence'}).click();
 await expect(page.getByRole('heading',{name:'Recorded V2 production / V1 observation'})).toBeVisible();
 await page.getByRole('button',{name:'Config Admin'}).click();
 await page.getByPlaceholder('Admin credential (memory only)').fill('test-admin');
 await page.getByRole('button',{name:'Load Active & Draft'}).click();
 await expect(page.getByText('BUNDLED_DEFAULT')).toBeVisible();
 await expect(page.getByRole('button',{name:'Authorize advisory profile'})).toBeDisabled();
 const persisted=await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length}));
 expect(persisted).toEqual({local:0,session:0});
});

test('late response from previous viewer credential never repopulates the console',async({page})=>{
 await page.route('**/api/**',async route=>{
  await new Promise(resolve=>setTimeout(resolve,450));
  const path=new URL(route.request().url()).pathname;
  const data=path==='/api/opip/scoring-comparison'
    ?{status:'RECORDED',rows:[{scan_id:'SCAN:old-secret',model_authority:'V2_PRODUCTION_V1_OBSERVATION'}]}
    :{status:'RECORDED',source:'old-secret'};
  await route.fulfill({json:data}).catch(()=>{});
 });
 await page.goto('/dashboard/v2/');
 const viewer=page.getByPlaceholder('X-Webhook-Secret (session memory only)');
 await viewer.fill('old-secret');
 const outgoing=page.waitForRequest(req=>req.url().includes('/api/analytics/summary'));
 await page.getByRole('button',{name:'Refresh'}).click();
 await outgoing;
 await viewer.fill('new-secret');
 await page.waitForTimeout(850);
 await expect(page.getByText('SCAN:old-secret')).toHaveCount(0);
 await expect(page.getByText('AWAITING EVIDENCE')).toBeVisible();
});

test('configuration preview includes deleted fields, and stale draft save never repopulates a new credential session',async({page})=>{
 const hash='a'.repeat(64);
 let resolveSave;
 const saveStarted=new Promise(resolve=>resolveSave=resolve);
 await page.route('**/api/**',async route=>{
  const request=route.request();
  const name=new URL(request.url()).pathname;
  if(name==='/api/opip/strategy-admin/draft' && request.method()==='PUT'){
   resolveSave();
   await new Promise(resolve=>setTimeout(resolve,550));
   await route.fulfill({json:{status:'DRAFT_SAVED',digest:'b'.repeat(64)}}).catch(()=>{});
   return;
  }
  if(name==='/api/opip/strategy-admin/draft' || name==='/api/opip/strategy-admin/active'){
   const document={scoring:{v2_threshold:75},ai_advisory:{enabled:false},advisory_economics:{},protection:{}};
   await route.fulfill({json:{status:'REFERENCE_ONLY',digest:hash,document}});
   return;
  }
  await route.fulfill({status:404,json:{detail:'Unavailable'}});
 });
 await page.goto('/dashboard/v2/');
 const viewer=page.getByPlaceholder('X-Webhook-Secret (session memory only)');
 await viewer.fill('first-viewer');
 await page.getByRole('button',{name:'Config Admin'}).click();
 await page.getByPlaceholder('Admin credential (memory only)').fill('first-admin');
 await page.getByRole('button',{name:'Load Active & Draft'}).click();
 await expect(page.getByText('Loaded server-side advisory configuration.')).toBeVisible();
 const json=page.getByRole('textbox',{name:'Strategy JSON draft'});
 const edited=JSON.parse(await json.inputValue());
 delete edited.ai_advisory;
 edited.scoring.v2_threshold=77;
 await json.fill(JSON.stringify(edited,null,2));
 await page.getByRole('button',{name:'Preview changes'}).click();
 await expect(page.getByText('ai_advisory',{exact:true})).toBeVisible();
 await expect(page.getByText('scoring.v2_threshold',{exact:true})).toBeVisible();
 await page.getByPlaceholder('Why are you changing this?').fill('review test');
 await page.getByRole('button',{name:'Stage draft'}).click();
 await saveStarted;
 await viewer.fill('second-viewer');
 await page.waitForTimeout(800);
 await expect(page.getByText('Draft staged without runtime activation.',{exact:false})).toHaveCount(0);
 await expect(json).toHaveCount(0);
});
