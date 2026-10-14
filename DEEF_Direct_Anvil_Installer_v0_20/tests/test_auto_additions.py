from pathlib import Path
import hashlib
import json
import zipfile
import auto_additions as aa


def item(i,archive,member,kind='data',data=None,obj=None,forge='C:/Game/DataPC.forge'):
    return {'index':i,'sourceArchive':archive,'member':member,
            'kind':kind,'parentData':data or member,'newObject':obj or '',
            'parentForge':forge, 'classification':'NEW_FORGE_DATA_ENTRY_CANDIDATE'}


def test_distinct_parent_ids_and_instruction():
    rows=[item(1,'a.zip','1_-_Foo.data/1_-_Obj.Unit',kind='inner',data='1_-_Foo.data',obj='1_-_Obj.Unit'),
          item(2,'b.zip','99_-_Foo.data/1_-_Obj.Unit',kind='inner',data='99_-_Foo.data',obj='1_-_Obj.Unit'),
          item(3,'c.zip','Unit.obj',kind='inner',data='Put in 0_-_Home.data',obj='1_-_Obj.Unit')]
    result=aa.candidates({'candidates':rows})
    assert result[0]['status']=='PENDING'
    assert result[1]['status']=='PENDING'
    assert result[2]['parentData']=='0_-_Home.data'
    assert result[2]['status']=='PENDING'


def test_outer_data_variants_share_forge_identity():
    rows=[item(1,'a.zip','1_-_Foo.data'),item(2,'b.zip','99_-_Foo.data')]
    result=aa.candidates({'candidates':rows})
    assert [x['reason'] for x in result]==['MULTIPLE_MODS_SAME_RESOURCE']*2


def test_same_exact_parent_resource_conflicts():
    rows=[item(1,'a.zip','1_-_Foo.data',kind='inner',data='1_-_Foo.data',obj='1_-_Obj.Unit'),
          item(2,'b.zip','1_-_Foo.data/1_-_Obj.Unit',kind='inner',data='1_-_Foo.data',obj='1_-_Obj.Unit')]
    result=aa.candidates({'candidates':rows})
    assert [x['reason'] for x in result]==['MULTIPLE_MODS_SAME_RESOURCE']*2


def test_skips_disabled_and_existing_replacements():
    rows=[item(1,'DISABLED.zip','123_-_B.data'),item(2,'open.zip','2_-_A.data'),
          item(3,'safe.zip','3_-_C.data')]
    result=aa.candidates({'candidates':rows},
        [{'forge':'C:/Game/DataPC.forge','data':'2_-_A.data','kind':'data'}],
        ['disabled.zip'])
    assert [x['reason'] for x in result]==['MOD_DISABLED','CONFLICTS_WITH_STAGED_REPLACEMENT','']


def test_unsafe_member_rejected():
    row=item(1,'a.zip','../payload.data')
    assert aa.candidates({'candidates':[row]})[0]['reason']=='UNSAFE_MEMBER'


def test_outer_and_inner_stage_preserves_game(tmp_path,monkeypatch):
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'Original archive')
    mods=tmp_path/'mods';mods.mkdir();(mods/'one.zip').write_bytes(b'fake zip')
    payload=tmp_path/'RESOURCE';payload.write_bytes(b'mod payload')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_bytes(b'dll')
    audit=tmp_path/'NEW_RESOURCE_AUDIT.json'
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),
         'candidates':[item(1,'one.zip','0_-_NewContainer.data',forge=str(forge)),
         item(2,'one.zip','Objects/1_-_OtherType.Unit',kind='inner',
              data='Put in 0_-_NewContainer.data',obj='1_-_OtherType.Unit',forge=str(forge))]}))
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa.mm,'extraction_target',lambda archive,member:payload)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    original=forge.read_bytes()
    def inspector(current):
        return ['0_-_NewContainer.data'] if b'NEWOUTER' in Path(current).read_bytes() else []
    def runner(command,receipt,output):
        mode=command[2]
        request=Path(command[-2] if mode=='stream-add-outer-trial' else command[-3])
        assert request.is_file()
        src=Path(command[4]);assert src.is_file()
        data=src.read_bytes()+ (b'NEWOUTER' if mode=='stream-add-outer-trial' else b'NEWINNER')
        output.write_bytes(data)
        receipt.write_text(json.dumps({'verified':True,'independentAnvilIndexVerified':True,
            'sourceUnchanged':True,'gameModified':False,'eligibleForDeployment':True,'addedEntries':1,
            'sourceForge':str(src),'outputSha256':hashlib.sha256(data).hexdigest()}))
        return 0
    manifest,result=aa.stage(audit,game,mods,toolkit,bridge_runner=runner,inspector=inspector,notify=lambda x:None)
    assert result['verifiedAdditionCount']==2
    assert result['excludedCount']==0
    assert forge.read_bytes()==original
    state=json.loads(Path(manifest).read_text())
    assert state['status']=='VERIFIED_STAGED'
    assert Path(state['outputs'][str(forge.resolve())]['staged']).read_bytes().endswith(b'NEWOUTERNEWINNER')
    assert state['outputs'][str(forge.resolve())]['sourceSha256']==hashlib.sha256(original).hexdigest()


def test_rejected_readback_keeps_original_and_exclusion(tmp_path,monkeypatch):
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_text('exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'original')
    mods=tmp_path/'mods';mods.mkdir();(mods/'one.zip').write_text('')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_text('')
    payload=tmp_path/'p';payload.write_bytes(b'payload')
    audit=tmp_path/'audit.json'
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),
            'candidates':[item(1,'one.zip','0_-_NewContainer.data',forge=str(forge))]}))
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa.mm,'extraction_target',lambda archive,member:payload)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    def reject(command,receipt,out):
        receipt.write_text(json.dumps({'verified':False,'error':'AnvilToolkit rejects directory'}))
        return 1
    manifest,result=aa.stage(audit,game,mods,toolkit,bridge_runner=reject,
        inspector=lambda x:[],notify=lambda x:None)
    assert result['verifiedAdditionCount']==0
    assert 'OUTER_INSERTION_NOT_VERIFIED' in result['excluded'][0]['reason']
    assert json.loads(Path(manifest).read_text())['status']=='NO_INSTALLABLE_CHANGES'
    assert forge.read_bytes()==b'original'


def test_missing_parent_is_repacked_then_added_as_outer_data(tmp_path,monkeypatch):
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_text('exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'original')
    mods=tmp_path/'mods';mods.mkdir();archive=mods/'new.zip'
    member='Mod/DataPC.forge/0_-_NewContainer.data/1_-_NewObject.Unit'
    with zipfile.ZipFile(archive,'w') as z:z.writestr(member,b'nested-object')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_text('dll')
    audit=tmp_path/'audit.json'
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),
        'candidates':[item(1,'new.zip',member,kind='inner',data='0_-_NewContainer.data',
                           obj='1_-_NewObject.Unit',forge=str(forge))]}))
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    def pack(command,receipt,output):
        source=Path(command[4]);assert (source/'1_-_NewObject.Unit').read_bytes()==b'nested-object'
        data=b'packed-container';output.write_bytes(data)
        receipt.write_text(json.dumps({'verified':True,'sourceUnchanged':True,'gameModified':False,
            'serializedFileCount':1,'readbackFileCount':1,'rootIdentityVerified':True,
            'outputSha256':hashlib.sha256(data).hexdigest()}))
        return 0
    def bridge(command,receipt,output):
        request=Path(command[-2]);additions=json.loads(request.read_text())
        assert additions==[{'data':'0_-_NewContainer.data','payload':additions[0]['payload']}]
        source=Path(command[4]);data=source.read_bytes()+b'+OUTER';output.write_bytes(data)
        receipt.write_text(json.dumps({'verified':True,'independentAnvilIndexVerified':True,
            'sourceUnchanged':True,'gameModified':False,'eligibleForDeployment':True,
            'addedEntries':1,'sourceForge':str(source),
            'outputSha256':hashlib.sha256(data).hexdigest()}))
        return 0
    manifest,result=aa.stage(audit,game,mods,toolkit,bridge_runner=bridge,pack_runner=pack,
        inspector=lambda current:['0_-_NewContainer.data'] if b'+OUTER' in Path(current).read_bytes() else [],
        notify=lambda _:None)
    assert result['verifiedAdditionCount']==1 and result['excludedCount']==0
    assert result['packing'][0]['verified'] is True
    assert forge.read_bytes()==b'original'
    state=json.loads(Path(manifest).read_text())
    assert state['status']=='VERIFIED_STAGED'


def test_base_replacement_stage_is_reused_not_overwritten(tmp_path,monkeypatch):
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_text('exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'ORIGINAL')
    mods=tmp_path/'mods';mods.mkdir();(mods/'a.zip').write_bytes(b'archive')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_bytes(b'dll')
    stagefolder=tmp_path/'batch';(stagefolder/'VerifiedForges').mkdir(parents=True)
    prev=stagefolder/'VerifiedForges'/'DataPC.forge';prev.write_bytes(b'ORIGINAL+VERIFIED_REPLACEMENT')
    receipt=stagefolder/'PRIOR_RESULT.json';receipt.write_text('{}')
    base=stagefolder/'BATCH_RESULT.json'
    source_hash=hashlib.sha256(forge.read_bytes()).hexdigest()
    old_sha=hashlib.sha256(prev.read_bytes()).hexdigest()
    prior={'schema':3,'status':'VERIFIED_STAGED','writer':'FORGE_STREAM_BATCH_V27',
           'plan':{'gameRoot':str(game),'sourceHashes':{str(forge):source_hash},'routes':[]},
           'outputs':{str(forge):{'sourceSha256':source_hash,'staged':str(prev),
               'stagedSha256':old_sha,'streamReceipt':str(receipt),'relative':forge.name}},
           'finishedRoutes':[],'excludedRoutes':[]}
    base.write_text(json.dumps(prior))
    audit=tmp_path/'NEW_RESOURCE_AUDIT.json'
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),
            'candidates':[item(1,'a.zip','10_-_New.data',forge=str(forge))]}))
    payload=tmp_path/'resource';payload.write_bytes(b'payload')
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa.mm,'extraction_target',lambda arc,name:payload)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    monkeypatch.setattr(aa.existing_stage,'inspect_existing_stage',lambda m,expected_game:None)
    def runner(command,receipt_file,out):
        source=Path(command[4]);assert source.read_bytes()==b'ORIGINAL+VERIFIED_REPLACEMENT'
        data=source.read_bytes()+b'+NEW';out.write_bytes(data)
        receipt_file.write_text(json.dumps({'verified':True,'eligibleForDeployment':True,
          'independentAnvilIndexVerified':True,'sourceUnchanged':True,'gameModified':False,
          'addedEntries':1,'sourceForge':str(source),
          'outputSha256':hashlib.sha256(data).hexdigest()}))
        return 0
    new_manifest,result=aa.stage(audit,game,mods,toolkit,base_manifest=base,
                bridge_runner=runner,inspector=lambda x:[],notify=lambda x:None)
    assert result['verifiedAdditionCount']==1
    assert prev.read_bytes()==b'ORIGINAL+VERIFIED_REPLACEMENT'
    assert forge.read_bytes()==b'ORIGINAL'
    assert json.loads(base.read_text())==prior
    state=json.loads(Path(new_manifest).read_text())
    combined=Path(state['outputs'][str(forge)]['staged'])
    assert combined.read_bytes()==b'ORIGINAL+VERIFIED_REPLACEMENT+NEW'
    assert Path(new_manifest).parent==base.parent


def test_private_receipt_never_qualifies(tmp_path):
    from auto_additions import _receipt_ok
    p=tmp_path/'a';p.write_bytes(b'data')
    x={'verified':True,'eligibleForDeployment':False,'independentAnvilIndexVerified':True,
        'sourceUnchanged':True,'gameModified':False,'addedEntries':1,
        'sourceForge':str(p),'outputSha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    assert _receipt_ok(x,p,p,1) is False


def test_repeated_unknown_forge_directory_index_is_reported_as_new_asset(tmp_path,monkeypatch):
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_text('exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'original forge')
    mods=tmp_path/'mods';mods.mkdir();(mods/'a.zip').write_bytes(b'archive')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_bytes(b'dll')
    payload=tmp_path/'payload.data';payload.write_bytes(b'new mod container')
    audit=tmp_path/'audit.json'
    rows=[item(i,'a.zip',f'{i}_-_New_{i}.data',forge=str(forge)) for i in range(1,5)]
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),'candidates':rows}))
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa.mm,'extraction_target',lambda archive,member:payload)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    def reject_new_directory(command,receipt,out):
        receipt.write_text(json.dumps({'verified':False,
            'error':'AnvilToolkit returned NULL Directories for synthesized Forge'}))
        return 1
    _,result=aa.stage(audit,game,mods,toolkit,bridge_runner=reject_new_directory,
        inspector=lambda current:[],notify=lambda _:None)
    assert len(result['attempted'])<=6
    assert result['verifiedAdditionCount']==0
    assert all(row['reason']=='NEW_MOD_ASSET_UNSUPPORTED_BY_ANVIL_TOOLKIT_DIRECTORY_INDEX'
               for row in result['excluded'])
    assert forge.read_bytes()==b'original forge'


def test_combined_addition_receipt_is_reusable_by_existing_stage_inspector(tmp_path,monkeypatch):
    import existing_stage
    game=tmp_path/'game';game.mkdir();(game/'GRB.exe').write_text('exe')
    forge=game/'DataPC.forge';forge.write_bytes(b'ORIGINAL')
    mods=tmp_path/'mods';mods.mkdir();(mods/'a.zip').write_bytes(b'archive')
    toolkit=tmp_path/'toolkit';toolkit.mkdir();(toolkit/'AnvilToolkit.dll').write_bytes(b'dll')
    folder=tmp_path/'base';(folder/'VerifiedForges').mkdir(parents=True)
    previous=folder/'VerifiedForges'/'DataPC.forge';previous.write_bytes(b'ORIGINAL+REPLACEMENT')
    source_hash=hashlib.sha256(forge.read_bytes()).hexdigest()
    previous_hash=hashlib.sha256(previous.read_bytes()).hexdigest()
    base_receipt=folder/'STREAM_RESULT.json'
    base_receipt.write_text(json.dumps({'verified':True,'originalSourceUnchanged':True,
        'prefetchBytesPreserved':True,'untouchedBytesVerified':True,
        'independentAnvilIndexVerified':True,'allRequestedEditsVerified':True,
        'requestedEdits':1,'stagedForgeSha256':previous_hash}))
    base=folder/'BATCH_RESULT.json'
    route={'forge':str(forge),'data':'0_-_Existing.data','object':'1_-_Object.Unit','member':'Object.Unit'}
    base.write_text(json.dumps({'schema':3,'writer':'FORGE_STREAM_BATCH_V27','status':'VERIFIED_STAGED',
        'plan':{'gameRoot':str(game),'sourceHashes':{str(forge):source_hash},'routes':[route]},
        'finishedRoutes':[route],'excludedRoutes':[],
        'outputs':{str(forge):{'sourceSha256':source_hash,'staged':str(previous),
            'stagedSha256':previous_hash,'relative':forge.name,'streamReceipt':str(base_receipt)}}}))
    audit=tmp_path/'audit.json';payload=tmp_path/'new.data';payload.write_bytes(b'new payload')
    audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),
        'candidates':[item(1,'a.zip','0_-_New.data',forge=str(forge))]}))
    monkeypatch.setattr(aa,'ROOT',tmp_path)
    monkeypatch.setattr(aa.mm,'extraction_target',lambda archive,member:payload)
    monkeypatch.setattr(aa,'_disabled_mods',lambda *args:[])
    def accept(command,receipt,out):
        source=Path(command[4]);data=source.read_bytes()+b'+NEW';out.write_bytes(data)
        receipt.write_text(json.dumps({'verified':True,'eligibleForDeployment':True,
            'independentAnvilIndexVerified':True,'sourceUnchanged':True,'gameModified':False,
            'addedEntries':1,'sourceForge':str(source),
            'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'output':str(out),'outputSha256':hashlib.sha256(data).hexdigest()}))
        return 0
    manifest,result=aa.stage(audit,game,mods,toolkit,base_manifest=base,
        bridge_runner=accept,inspector=lambda current:[],notify=lambda _:None)
    assert result['verifiedAdditionCount']==1
    summary=existing_stage.inspect_existing_stage(manifest,expected_game=game)
    assert summary['acceptedEdits']==1
    assert summary['excludedEdits']==0
