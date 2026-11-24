const fs=require('node:fs'), floor=require('../../backend/maps_data/school-building-01/2026-11-22.1.json').floors['1'];
const nodes=new Map(floor.topology.nodes.map(n=>[n.id,n.coordinates]));
const walls=floor.geometry.walls.flatMap(w=>w.geometry.coordinates.slice(1).map(([bx,by],i)=>{
 const [ax,ay]=w.geometry.coordinates[i],dx=bx-ax,dy=by-ay,l=Math.hypot(dx,dy);
 return {id:w.id,ax,ay,dx:dx/l,dy:dy/l,l,h:(w.properties.thickness>0?w.properties.thickness:.2)/2};
})).filter(w=>w.l);
function clearance(x,y){
 let min=Infinity;
 for(const {ax,ay,dx,dy,l,h} of walls){
  const u=x-ax,v=y-ay,t=Math.max(0,Math.min(l,u*dx+v*dy));
  min=Math.min(min,Math.hypot(u-t*dx,v-t*dy)-h);
 }
 return min;
}
function rayEntry(x,y,rx,ry,{ax,ay,dx,dy,l,h}){
 const u=x-ax,v=y-ay;
 let entry=0,exit=Infinity;
 for(const [position,speed,min,max] of [
  [u*dx+v*dy,rx*dx+ry*dy,0,l],
  [-u*dy+v*dx,-rx*dy+ry*dx,-h,h],
 ]){
  if(Math.abs(speed)<1e-10){
   if(position<min||position>max)return Infinity;
   continue;
  }
  const t=(min-position)/speed,s=(max-position)/speed;
  entry=Math.max(entry,Math.min(t,s));
  exit=Math.min(exit,Math.max(t,s));
 }
 return entry<=exit&&exit>0?entry:Infinity;
}
function firstHit(x,y,rx,ry,segments){
 let first;
 let distance=Infinity;
 for(const segment of segments){
  const entry=rayEntry(x,y,rx,ry,segment);
  if(entry<distance){
   distance=entry;
   first={id:segment.id,dist:entry};
  }
 }
 return first;
}
const inside=(x,y,ring)=>ring.reduce((hit,[a,b],i)=>{const [c,d]=ring[(i+1)%ring.length];return (b>y)!==(d>y)&&x<(c-a)*(y-b)/(d-b)+a?!hit:hit;},false);
function publicCollectionArea(x,y){
 const hits=floor.geometry.rooms.filter(r=>r.geometry.type==='Polygon'&&inside(x,y,r.geometry.coordinates[0])&&!r.geometry.coordinates.slice(1).some(h=>inside(x,y,h)));
 // Only the route junction's public lobby is a candidate for boundary review.
 return hits.length>0&&hits.every(r=>r.properties.public===true&&(
  ['corridor','activity','stair_lobby','elevator_lobby'].includes(r.properties.roomType)||r.id==='F1-LB-0045'
 ));
}
const pathNodes='F1-TR-0016 F1-TD-0020 F1-TI-0020 F1-TI-0011 F1-TI-0013 F1-TI-0111 F1-TI-0027 F1-TI-0028 F1-TI-0072 F1-TI-0007 F1-TI-0060 F1-TI-0054 F1-TI-0058 F1-TI-0057 F1-TI-0009 F1-TI-0056 F1-TI-0055 F1-TI-0101 F1-TD-0014 F1-TR-0003'.split(' ');
const edges=pathNodes.slice(1).map((to,i)=>{
 const from=pathNodes[i];
 if(!floor.topology.edges.some(e=>e.walkable&&[e.from,e.to].includes(from)&&[e.from,e.to].includes(to)))throw Error(from+' '+to);
 const [ax,ay]=nodes.get(from),[bx,by]=nodes.get(to),len=Math.hypot(bx-ax,by-ay);
 return {ax,ay,bx,by,len};
});

const doors=[...floor.geometry.doors,...(floor.geometry.elevatorDoors??[])].filter(d=>d.geometry.type==='Point');
function doorOpening(door){
 const [x,y]=door.geometry.coordinates;
 const axis=door.properties.axis;
 if(axis){
  const [[ax,ay],[bx,by]]=axis;
  const l=Math.hypot(bx-ax,by-ay);
  if(l>0)return {id:door.id,ax,ay,dx:(bx-ax)/l,dy:(by-ay)/l,l,h:.15};
 }
 const l=Number(door.properties.width_m)||1;
 let support;
 let distance=Infinity;
 for(const wall of walls){
  const u=x-wall.ax,v=y-wall.ay;
  const along=u*wall.dx+v*wall.dy;
  const normal=-u*wall.dy+v*wall.dx;
  // The wall may end at a jamb. Score against the full opening width
  // so the short wall across the jamb does not determine its direction.
  const offset=Math.hypot(normal,Math.max(0,-along-l/2,along-wall.l-l/2));
  if(offset<distance){
   distance=offset;
   support=wall;
  }
 }
 // Older map packages store regular openings as a center and width. Use the
 // wall aligned with the opening and cover its faces across the span.
 const {dx,dy,h}=support;
 const normalOffset=Math.abs(-(x-support.ax)*dy+(y-support.ay)*dx);
 const halfDepth=Math.min(.3,normalOffset+h)+1e-6;
 return {id:door.id,ax:x-dx*l/2,ay:y-dy*l/2,dx,dy,l,h:halfDepth};
}
const doorOpenings=doors.map(doorOpening);
function firstDoorCrossing(x,y,rx,ry){
 let first;
 for(const opening of doorOpenings){
  const {ax,ay,dx,dy,l,h}=opening;
  const normalPosition=-(x-ax)*dy+(y-ay)*dx;
  const normalSpeed=-rx*dy+ry*dx;
  // Cross the actual opening span. Thickness only aligns the crossing
  // with the near wall face, rather than inflating the opening sideways.
  let entry;
  if(Math.abs(normalSpeed)<1e-10){
   if(Math.abs(normalPosition)>1e-9)continue;
   entry=rayEntry(x,y,rx,ry,{...opening,h:1e-9});
   if(!Number.isFinite(entry))continue;
  }else{
   const crossing=-normalPosition/normalSpeed;
   const along=(x+crossing*rx-ax)*dx+(y+crossing*ry-ay)*dy;
   if(crossing<0||along<0||along>l)continue;
   entry=Math.max(0,crossing-h/Math.abs(normalSpeed));
  }
  if(!first||entry<first.dist)first={id:opening.id,dist:entry};
 }
 return first;
}

const grid=require('../../result/fingerprint_grid.json');
const origin=grid.origin;
const neighbors=new Map();
for(const {from,to,walkable} of floor.topology.edges){
 if(!walkable)continue;
 for(const [a,b] of [[from,to],[to,from]]){
  if(!neighbors.has(a))neighbors.set(a,new Set());
  neighbors.get(a).add(b);
 }
}
const staircaseIds=new Set(floor.geometry.rooms.filter(r=>r.properties.roomType==='staircase').map(r=>r.id));
const denseLobbies=floor.geometry.rooms.filter(r=>['elevator_lobby','stair_lobby'].includes(r.properties.roomType)&&r.properties.public);
const denseAnchors=[
 ...floor.topology.nodes.filter(n=>n.type==='intersection'&&(neighbors.get(n.id)?.size??0)>=3)
  .map(n=>({coordinates:n.coordinates,reason:'intersection'})),
 ...doors.map(d=>({coordinates:d.geometry.coordinates,
  reason:d.properties.rooms?.some(id=>staircaseIds.has(id))?'high_risk':'doorway'})),
 ...floor.topology.nodes.filter(n=>n.roomType==='staircase'||n.facilityType==='staircase')
  .map(n=>({coordinates:n.coordinates,reason:'high_risk'})),
];

function denseCellReason(left,bottom){
 const right=left+2,top=bottom+2;
 if(denseLobbies.some(lobby=>{
  const ring=lobby.geometry.coordinates[0];
  return ring.some(([x,y])=>x>=left&&x<=right&&y>=bottom&&y<=top)||
   [[left,bottom],[right,bottom],[left,top],[right,top],[left+1,bottom+1]].some(([x,y])=>inside(x,y,ring));
 }))return 'high_risk';
 for(const {coordinates:[x,y],reason} of denseAnchors){
  const dx=Math.max(left-x,0,x-right),dy=Math.max(bottom-y,0,y-top);
  if(Math.hypot(dx,dy)<=1)return reason;
 }
}

function gridRegion(col,row){
 const left=origin.x+2*Math.floor(col/2),bottom=origin.y+2*Math.floor(row/2);
 const columns=col%2===0?[left-2,left]:[left];
 const rows=row%2===0?[bottom-2,bottom]:[bottom];
 for(const x of columns)for(const y of rows){
  const reason=denseCellReason(x,y);
  if(reason)return reason;
 }
}
const testPoints=fs.readFileSync('docs/field-survey-candidates.csv','utf8').split(/\r?\n/).slice(2)
 .filter(Boolean).map(line=>line.slice(1,-1).split('","')).filter(r=>r[1]==='static_test');
function distanceToRoute(x,y){
 return Math.min(...edges.map(e=>{
  const dx=e.bx-e.ax,dy=e.by-e.ay,u=x-e.ax,v=y-e.ay;
  const t=Math.max(0,Math.min(1,(u*dx+v*dy)/(e.len*e.len)));
  return Math.hypot(u-t*dx,v-t*dy);
 }));
}
const probeDirections=[
 {direction:'+X',dx:1,dy:0},
 {direction:'-X',dx:-1,dy:0},
 {direction:'+Y',dx:0,dy:1},
 {direction:'-Y',dx:0,dy:-1},
];
function probeWalls(x,y){
 return probeDirections.map(({direction,dx,dy})=>{
  const hit=firstHit(x,y,dx,dy,walls);
  const door=firstDoorCrossing(x,y,dx,dy);
  if(door&&(!hit||door.dist<=hit.dist)){
   return {direction,status:'door_blocked',doorId:door.id};
  }
  if(!hit)return {direction,status:'no_hit'};
  return {
   direction,status:hit.dist<.005?'too_close':'usable',id:hit.id,distanceM:Number(hit.dist.toFixed(2)),
   hitX:Number((x+dx*hit.dist).toFixed(3)),
   hitY:Number((y+dy*hit.dist).toFixed(3)),
  };
 });
}

function nearestUsableProbe(probes,axis){
 return probes.filter(probe=>probe.direction.endsWith(axis)&&probe.status==='usable')
  .sort((a,b)=>a.distanceM-b.distanceM)[0];
}
const points=[];
const junctionLobby=floor.geometry.rooms.find(r=>r.id==='F1-LB-0045');
for(const p of grid.floors['1'].points){
 const {x,y}=p;
 const isBase2m=p.col%2===0&&p.row%2===0;
 const region=gridRegion(p.col,p.row);
 if(!p.collectablePlan10m||(!isBase2m&&!region)||!publicCollectionArea(x,y))continue;
 if(testPoints.some(r=>Math.hypot(x-Number(r[7]),y-Number(r[8]))<.20))continue;
 const wallProbes=probeWalls(x,y);
 const a=nearestUsableProbe(wallProbes,'X');
 const b=nearestUsableProbe(wallProbes,'Y');
 const issues=[];
 if(clearance(x,y)<.5)issues.push('离墙净距不足 0.50 m');
 if(!a||!b)issues.push('缺少可直测且不穿门的双墙参照');
 if(junctionLobby&&inside(x,y,junctionLobby.geometry.coordinates[0]))issues.push('公共门厅采集边界待确认');
 const collectable=issues.length===0;
 points.push({
  fpId:p.fpId,floorId:'1',x,y,regionType:region??'normal',shortId:p.shortId,
  col:p.col,row:p.row,isBase2m,collectable,collectable15m:collectable&&p.collectableGeo15m,
  collectableWithSim:collectable,simAddedBy:[],routeTags:distanceToRoute(x,y)<=2?['R1']:[],
  visible10m:p.visible10m,visible15m:p.visible15m,nearestBeaconM:p.nearestBeaconM,strongestRssi:p.strongestRssi,
  references:[a,b].filter(Boolean).map(({id,direction,distanceM,hitX,hitY})=>({id,direction,distanceM,hitX,hitY})),
  wallProbes,
  ...(collectable?{}:{placementIssue:issues.join(' · ')}),
 });
}
console.log('public coverage / ready / route priority:',points.length,
 points.filter(p=>p.collectable).length,points.filter(p=>p.routeTags.length).length);
if(!points.length||points.length>256||new Set(points.map(p=>p.fpId)).size!==points.length)throw Error('Invalid field grid size or duplicate point IDs');
if(new Set(points.map(p=>p.x+','+p.y)).size!==points.length ||
   points.some(p=>p.x!==origin.x+p.col||p.y!==origin.y+p.row||
    !Number.isInteger(p.col)||!Number.isInteger(p.row)||
    (p.isBase2m&&(p.col%2!==0||p.row%2!==0))||
    (!p.isBase2m&&!gridRegion(p.col,p.row))))throw Error('Field grid is not aligned');
const counts=points.reduce((a,p)=>(a[p.isBase2m?0:1]++,a),[0,0]);
console.log('2m / 1m:',counts,'maximum laser distance:',Math.max(...points.flatMap(p=>p.references.map(r=>r.distanceM))));
const output=[
 '// Generated by src/tools/generate_field_fingerprint_grid.cjs.',
 "import type { FpGridPoint } from './fingerprint_grid';",
 '',
 'type WallReference = { id: string; direction: string; distanceM: number; hitX: number; hitY: number };',
 'type WallProbe = Partial<WallReference> & { direction: string; status: string; doorId?: string };',
 "export const FIELD_GRID_VERSION = 't4-f1-20261119.2';",
 'export const FIELD_FINGERPRINT_GRID: Array<FpGridPoint & { references: WallReference[]; wallProbes: WallProbe[]; placementIssue?: string }> = [',
 ...points.map(p=>'  '+JSON.stringify(p)+','),
 '];','',
].join('\n');
if(process.argv.includes('--write'))fs.writeFileSync('client/data/field-fingerprint-grid.ts',output);

