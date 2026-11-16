"""Native PhysX cooking, capsule overlap control, and dynamic drop/settle trials."""
from pathlib import Path
import json,time,math,hashlib
import numpy as np
ROOT=Path('/cephfs/shared/yixing/outputs/museum29_20261107')
def run(app,job):
    import sys,asyncio
    rep=sys.modules.get('omni.replicator.core')
    if rep is not None:
        future=asyncio.ensure_future(rep.orchestrator.wait_until_complete_async() if rep.orchestrator.get_is_stopped() else rep.orchestrator.stop_async())
        while not future.done():app.update()
        future.result()
    import omni.usd,omni.timeline
    from omni.physx import get_physx_interface,get_physx_scene_query_interface,get_physx_cooking_private_interface,get_physxunittests_interface
    from pxr import Usd,UsdGeom,UsdPhysics,PhysxSchema,Gf,PhysicsSchemaTools,UsdShade
    source=Path(job['stage']);D=ROOT/job.get('output','C5');D.mkdir(exist_ok=True);sha=hashlib.sha256(source.read_bytes()).hexdigest();ctx=omni.usd.get_context();timeline=omni.timeline.get_timeline_interface();timeline.stop();assert ctx.open_stage(str(source))
    while ctx.get_stage_loading_status()[2]:app.update()
    stage=ctx.get_stage();stage.SetEditTarget(stage.GetSessionLayer());expected=sum(p.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False for p in stage.Traverse(Usd.TraverseInstanceProxies()))
    physics=UsdPhysics.Scene.Define(stage,'/MuseumC5Physics');physics.CreateGravityDirectionAttr(Gf.Vec3f(0,0,-1));physics.CreateGravityMagnitudeAttr(9.81);pa=PhysxSchema.PhysxSceneAPI.Apply(physics.GetPrim());pa.CreateEnableGPUDynamicsAttr(False);pa.CreateBroadphaseTypeAttr('MBP')
    probe=UsdGeom.Capsule.Define(stage,'/MuseumC5Probe');probe.CreateRadiusAttr(.25);probe.CreateHeightAttr(1.1);probe.CreateAxisAttr('Z');collision=UsdPhysics.CollisionAPI.Apply(probe.GetPrim());collision.CreateCollisionEnabledAttr(True);rb=UsdPhysics.RigidBodyAPI.Apply(probe.GetPrim());rb.CreateRigidBodyEnabledAttr(True);rb.CreateVelocityAttr(Gf.Vec3f(0));rb.CreateAngularVelocityAttr(Gf.Vec3f(0));mass=UsdPhysics.MassAPI.Apply(probe.GetPrim());mass.CreateMassAttr(5.);mass.CreateCenterOfMassAttr(Gf.Vec3f(0,0,-.6));pb=PhysxSchema.PhysxRigidBodyAPI.Apply(probe.GetPrim());pb.CreateAngularDampingAttr(10.);pb.CreateLinearDampingAttr(.5)
    trans=probe.AddTranslateOp();trans.Set(Gf.Vec3d(30,30,2));orient=probe.AddOrientOp();orient.Set(Gf.Quatf(1.,Gf.Vec3f(0)))
    material=UsdShade.Material.Define(stage,'/MuseumCapsuleMaterial');pm=UsdPhysics.MaterialAPI.Apply(material.GetPrim());pm.CreateStaticFrictionAttr(.9);pm.CreateDynamicFrictionAttr(.8);pm.CreateRestitutionAttr(0);UsdShade.MaterialBindingAPI.Apply(probe.GetPrim()).Bind(material,materialPurpose='physics')
    phys=get_physx_interface();interface=get_physx_scene_query_interface();cooking=get_physx_cooking_private_interface();simtime=0
    try:
        timeline.play()
        for _ in range(5):app.update()
        deadline=time.monotonic()+900
        while True:
            cooked=cooking.get_cooking_statistics()
            if cooked.total_scheduled_tasks<=cooked.total_finished_tasks:break
            assert time.monotonic()<deadline,'COOKING_TIMEOUT';app.update()
        for _ in range(3):app.update()
        native=get_physxunittests_interface().get_physics_stats();count=sum(int(native.get(k,0)) for k in ['numSphereShapes','numBoxShapes','numCapsuleShapes','numCylinderShapes','numConvexShapes','numTriMeshShapes','numPlaneShapes','numConeShapes']);assert count>=expected+1,'NATIVE_COLLIDER_REGISTRATION_INCOMPLETE'
        timeline.pause();phys.start_simulation()
        # A real authored capsule through the slab must produce a native overlap.
        neg=UsdGeom.Capsule.Define(stage,'/MuseumC5Negative');neg.CreateRadiusAttr(.25);neg.CreateHeightAttr(1.1);neg.CreateAxisAttr('Z');UsdPhysics.CollisionAPI.Apply(neg.GetPrim());neg.AddTranslateOp().Set(Gf.Vec3d(*job.get('negative_xyz',[1,1,-.10])))
        phys.update_simulation(1/120,simtime);simtime+=1/120;phys.update_transformations(False,True,True,False);hits=[]
        def hit(rec):
            if str(rec.collision)!=str(neg.GetPath()):hits.append(str(rec.collision))
            return True
        interface.overlap_shape(*PhysicsSchemaTools.encodeSdfPath(neg.GetPath()),hit,False);negative={'status':'FAIL_AS_REQUIRED' if any(job.get('negative_floor_name','Ground_Floor') in h for h in hits) else 'NEGATIVE_ESCAPE','actual_native_overlap_paths':sorted(set(hits)),'fixture':'static full-size capsule straddling the actual Floor1 slab'};assert negative['status']=='FAIL_AS_REQUIRED';stage.RemovePrim(neg.GetPath())
        rows=json.loads(Path(job['spawns']).read_text())['points']
        outputs=[]
        for row in rows:
            trans.Set(Gf.Vec3d(30,30,2));rb.GetVelocityAttr().Set(Gf.Vec3f(0));rb.GetAngularVelocityAttr().Set(Gf.Vec3f(0));phys.update_simulation(1/120,simtime);simtime+=1/120;phys.update_transformations(False,True,True,False)
            x,y=map(float,row['xy']);nominal=float(row['nominal_z']);ray=interface.raycast_closest((x,y,nominal+.30),(0.,0.,-1.),.6);assert ray['hit'],'NO_NATIVE_SUPPORT_RAY'
            foot=float(ray['position'][2]);initial=np.array([x,y,foot+.88]);trans.Set(Gf.Vec3d(*map(float,initial)));orient.Set(Gf.Quatf(1.,Gf.Vec3f(0)));rb.GetVelocityAttr().Set(Gf.Vec3f(0));rb.GetAngularVelocityAttr().Set(Gf.Vec3f(0));history=[]
            for k in range(300):
                phys.update_simulation(1/120,simtime);simtime+=1/120;phys.update_transformations(False,True,True,False)
                if k>=240:
                    pose=phys.get_rigidbody_transformation(str(probe.GetPath()));assert pose['ret_val'];history.append(pose)
            positions=np.array([p['position'] for p in history]);quat=np.array(history[-1]['rotation']);position=positions[-1];up=1-2*(quat[0]**2+quat[1]**2);foot_error=float(position[2]-.8-foot);drift=float(np.linalg.norm(position[:2]-[x,y]));jitter=float(np.ptp(positions,axis=0).max());speed=float(np.linalg.norm(positions[-1]-positions[-2])*120)
            drop=float(initial[2]-position[2]);passed=bool(-.03<=foot_error<=.03 and drop>=.05 and drift<=.20 and jitter<=.015 and speed<=.05 and up>=math.cos(math.radians(15)))
            result={**row,'actual_support_z_m':foot,'native_support_collider':str(ray.get('collision','')),'initial_native_center_m':initial.tolist(),'actual_gravity_drop_m':drop,'final_native_position_m':position.tolist(),'final_native_rotation_xyzw':quat.tolist(),'feet_error_from_support_m':foot_error,'horizontal_drift_m':drift,'last_0_5s_max_position_range_m':jitter,'final_speed_m_s':speed,'upright_cosine':float(up),'simulated_seconds':2.5,'status':'PASS' if passed else 'FAIL'};outputs.append(result);(D/'C5_PROGRESS.json').write_text(json.dumps({'completed':len(outputs),'total':len(rows),'last':result},indent=2));print('C5_SETTLE',len(outputs),len(rows),result['status'],foot_error,drift,jitter,flush=True)
        assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
        result={'status':'PASS' if all(r['status']=='PASS' for r in outputs) else 'FAIL','source_stage':str(source),'source_sha256':sha,'body_radius_m':.25,'body_total_height_m':1.6,'capsule_mass_kg':5.,'center_of_mass_offset_z_m':-.6,'gravity_m_s2':9.81,'expected_enabled_colliders':expected,'native_shape_count_including_capsule':count,'native_PhysX_statistics':native,'cooking':{'scheduled':cooked.total_scheduled_tasks,'finished':cooked.total_finished_tasks},'negative_control':negative,'trials':outputs,'source_stage_unchanged':True,'scope':'Dynamic drop/settle and native supports; not humanoid locomotion or learned-policy ascent'};(D/'C5.json').write_text(json.dumps(result,indent=2));return result
    finally:
        timeline.stop();phys.release_physics_objects();ctx.close_stage()
