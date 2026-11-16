"""Reject black/near-empty previews before they enter supervision outputs."""
from pathlib import Path
import hashlib
import numpy as np
from PIL import Image,ImageFilter
def check_image(path,collision=False):
 p=Path(path);im=Image.open(p).convert('RGB');a=np.asarray(im,dtype=np.float32);Y=a@np.array([.2126,.7152,.0722],dtype=np.float32);mean=float(Y.mean());std=float(Y.std());effective=float((Y>8).mean());small=im.copy();small.thumbnail((1600,1600),Image.Resampling.LANCZOS);gray=small.convert('L');g=np.asarray(gray,dtype=np.float32)
 def laplacian(x):return -4*x[1:-1,1:-1]+x[:-2,1:-1]+x[2:,1:-1]+x[1:-1,:-2]+x[1:-1,2:]
 lap=laplacian(g);sharp=float(lap.var());detail=None;sharp_ok=sharp>=10
 if collision:
  # Flat colors have little texture. Measure actual edges and their response
  # to the same sigma3 blur used by the required paired negative control.
  smooth=np.asarray(gray.filter(ImageFilter.GaussianBlur(.7)),dtype=np.float32);gradient=np.hypot((smooth[1:-1,2:]-smooth[1:-1,:-2])*.5,(smooth[2:,1:-1]-smooth[:-2,1:-1])*.5);edges=gradient>=4.;blurred=laplacian(np.asarray(gray.filter(ImageFilter.GaussianBlur(3.)),dtype=np.float32));energy=float(np.mean(lap[edges]**2)) if edges.any() else 0.;blur_energy=float(np.mean(blurred[edges]**2)) if edges.any() else 0.;ratio=energy/max(blur_energy,.001);sharp_ok=bool(edges.sum()>=100 and energy>=16 and ratio>=8)
  detail={'method':'edge-conditioned Laplacian energy / same-image Gaussian sigma3 energy','edge_pixels':int(edges.sum()),'edge_gradient_min':4.,'edge_energy':energy,'sigma3_energy':blur_energy,'energy_ratio':ratio,'thresholds':{'edge_pixels_min':100,'edge_energy_min':16,'energy_ratio_min':8},'status':'PASS' if sharp_ok else 'FAIL'}
 ok=mean>=8 and std>=4 and effective>=.10 and sharp_ok
 return {'status':'PASS' if ok else 'FAIL','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'resolution':list(im.size),'bytes':p.stat().st_size,'mean_luminance_0_255':mean,'std_luminance':std,'fraction_luminance_gt8':effective,'laplacian_variance_at_max1600px':sharp,'collision_edge_sharpness':detail,'thresholds':{'mean_min':8,'std_min':4,'effective_pixel_fraction_min':.10,'visual_laplacian_variance_min':10},'scope':'ADJUST06 edge/paired-blur gate for collision views; original global10 gate for visual images; actual content still requires visual review'}
