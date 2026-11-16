"""Small supported practical fixtures; separate invisible fill light cards."""
import math
def ceiling_fixture(B,name,x,y,ceiling,family,kelvin):
 material='Brass' if family in ['neoclassical','art_deco','victorian_natural'] else 'Wood' if family in ['nordic','east_asian'] else 'Steel'
 B.cylinder(name+'_CeilingRose',(x,y,ceiling-.07),.16,.07,material,False)
 if family in ['neoclassical','art_deco','victorian_natural']:
  B.tube(name+'_Stem',[(x,y,ceiling-.06),(x,y,ceiling-.55)],.018,material,False,'fixture')
  for i in range(3):
   a=i*math.tau/3;xx=x+.35*math.cos(a);yy=y+.35*math.sin(a);B.tube(name+f'_Arm{i}',[(x,y,ceiling-.48),(xx,yy,ceiling-.58)],.018,material,False,'fixture');B.cylinder(name+f'_Shade{i}',(xx,yy,ceiling-.76),.17,.22,material,False,.075);B.area(name+f'_Bulb{i}',(xx,yy,ceiling-.77),(xx,yy,ceiling-2),75,.10,(1,.95,.88),kelvin)
 elif family=='industrial':
  B.tube(name+'_Suspension',[(x,y,ceiling-.06),(x,y,ceiling-.43)],.015,material,False,'fixture');B.cylinder(name+'_EnamelBell',(x,y,ceiling-.69),.29,.26,material,False,.10);B.area(name+'_Lamp',(x,y,ceiling-.70),(x,y,ceiling-2),230,.16,(1,.94,.84),kelvin)
 elif family=='modern':
  B.box(name+'_Track',(x-.65,y-.035,ceiling-.07),(x+.65,y+.035,ceiling),'Steel',False,'fixture')
  for i,dx in enumerate([-.40,.40]):
   B.tube(name+f'_Bracket{i}',[(x+dx,y,ceiling-.06),(x+dx,y,ceiling-.18)],.015,material,False,'fixture');B.cylinder(name+f'_SpotHousing{i}',(x+dx,y,ceiling-.34),.08,.18,material,False);B.area(name+f'_Spot{i}',(x+dx,y,ceiling-.35),(x+dx,y,ceiling-2),90,.06,(1,1,1),kelvin)
 else:
  B.cylinder(name+'_SmallHousing',(x,y,ceiling-.26),.11,.20,material,False);B.area(name+'_Downlight',(x,y,ceiling-.27),(x,y,ceiling-2),110 if family=='marine' else 180,.08,(.72,.86,1) if family=='marine' else (1,1,1),kelvin)
 B.layout.append({'name':name,'kind':'fixture','family':family,'ceiling_anchor_xyz_m':[x,y,ceiling],'support':'visible ceiling rose, stem/housing meet actual supported slab; non-colliding high decoration','emitter_visibility':'hidden from camera/glossy/transmission; practical housing visible'})
