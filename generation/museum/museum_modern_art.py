"""Distinct original modern collections: color fields and monochrome line studies."""
import numpy as np
from PIL import Image,ImageDraw,ImageFilter

def artwork(B,index):
 bid=B.spec['building_id'];rng=np.random.default_rng(B.spec['seed']*1000+index);w,h=1024,768
 if bid=='M14':
  palette=np.array([[170,53,32],[42,67,115],[171,133,37],[38,103,82],[93,52,107],[166,111,89]],dtype=float);base=palette[index%len(palette)];yy,xx=np.mgrid[:h,:w];grain=rng.normal(0,1.1,(h,w,1));gradient=(yy/h-.5)*rng.uniform(9,22);a=np.clip(base+gradient[:,:,None]+grain,0,255).astype('uint8');im=Image.fromarray(a);d=ImageDraw.Draw(im);y=int(h*rng.uniform(.30,.72));d.rectangle((40,y,w-40,min(h-35,y+int(h*.22))),fill=tuple(int(x) for x in np.clip(base*.64+18,0,255)));im=im.filter(ImageFilter.GaussianBlur(7));style='Original restrained color-field painting, one broad soft-edged field and tonal band; no geometric collage or thin-line motif.'
 else:
  im=Image.new('RGB',(w,h),(225,224,219));d=ImageDraw.Draw(im)
  for j in range(90):
   x=np.linspace(30,w-30,180);phase=rng.uniform(0,6.28);y=h*.5+(j-45)*5+np.sin(x/w*6.28*rng.uniform(.7,1.3)+phase)*rng.uniform(12,65);shade=int(rng.uniform(28,130));d.line(list(zip(x.astype(int),y.astype(int))),fill=(shade,shade,shade),width=int(rng.integers(1,4)))
  style='Original monochrome flowing-line engraving study, grayscale only; no color-block abstraction.'
 p=B.tex/f'{bid}_OriginalDistinct17_{index:03}.png';im.save(p);return {'source':'Museum29 procedural original art','id':bid+f'_distinct17_{index:03}','title':style+' '+str(index),'license':'procedural','path':str(p),'dimensions':[w,h],'art_style17':style}
