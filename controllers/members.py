import stories_manager
from gluon.storage import Storage
from gluon.utils import web2py_uuid
from my_cache import Cache
import ws_messaging
from http_utils import json_to_storage
import datetime
import os
from dal_utils import insert_or_update
from photos import get_slides_from_photo_list, photos_folder, local_photos_folder, crop, save_uploaded_photo
import random
import zlib

@serve_json
def member_list(vars):
    return dict(member_list=get_member_names(vars.visible_only, vars.gender))

@serve_json
def get_member_details(vars):
    if not vars.member_id:
        raise User_Error(T('Member does not exist yet!'))
    if vars.member_id == "new":
        new_member = dict(
            member_info=Storage(
                first_name="",
                last_name="",
                former_first_name="",
                former_last_name="",
                full_name="members.new-member"),
            story_info = Storage(display_version='New Story', story_versions=[], story_text='', story_id=None),
            family_connections =  Storage(
                parents=dict(pa=None, ma=None),
                siblings=[],
                spouses=[],
                children=[]
            ),
            images = [],
            slides=[],
            spouses=[],
            facePhotoURL = request.application + '/static/images/dummy_face.png'
        )
        return new_member
    mem_id = int(vars.member_id)
    if vars.shift == 'next':
        mem_id += 1
    elif vars.shift == 'prev':
        mem_id -= 1
    member_info = get_member_rec(mem_id)
    if not member_info:
        raise User_Error(T('You reached the end of the list'))
    sm = stories_manager.Stories()
    story_info = sm.get_story(member_info.story_id) or Storage(display_version='New Story', story_versions=[], story_text='', story_id=None)
    family_connections = get_family_connections(member_info)
    slides = get_member_slides(mem_id)
    if member_info.gender == 'F':
        spouses = 'husband' + ('s' if len(family_connections.spouses) > 1 else '')
    else:
        spouses = 'wife' + ('s' if len(family_connections.spouses) > 1 else '')
    return dict(member_info=member_info, story_info=story_info, family_connections=family_connections, 
                ##images=images,
                slides=slides, #todo: duplicate?
                spouses = spouses,
                facePhotoURL = photos_folder('profile_photos') + member_info.facePhotoURL if  member_info.facePhotoURL else request.application + '/static/images/dummy_face.png')

@serve_json
def save_member_info(vars):
    story_info = vars.story_info
    if story_info:
        story_id = save_story_info(story_info, used_for=STORY4MEMBER)
    else:
        story_id = None
    member_id = vars.member_id
    member_info = vars.member_info
    if member_info:
        new_member = not member_info.id
        if story_id:
            member_info.story_id = story_id
        result = insert_or_update(db.TblMembers, **member_info)
        if isinstance(result, dict):
            return dict(errors=result['errors'])
        member_id = result
        member_rec = get_member_rec(member_id)
        member_rec = json_to_storage(member_rec)
        ws_messaging.send_message(key='MEMBER_LISTS_CHANGED', group='ALL_USERS', member_rec=member_rec, new_member=new_member)
    elif story_id:
        db(db.TblMembers.id==member_id).update(story_id=story_id)
    result = Storage(story_id=story_id)
    if member_id:
        result.member_id = member_id;
    return result

@serve_json
def upload_photos(vars):
    today = datetime.date.today()
    month = str(today)[:-3]

    path = local_photos_folder() + 'uploads/' + month + '/'
    number_uploaded = 0
    number_duplicates = 0
    failed = []
    if not os.path.isdir(path):
        os.makedirs(path)
    for fn in vars:
        fil = vars[fn]
        crc = zlib.crc32(fil.BINvalue)
        cnt = db(db.TblPhotos.crc==crc).count()
        if cnt > 0:
            number_duplicates += 1
            continue
        
        original_file_name, ext = os.path.splitext(fil.name)
        file_name = '{crc:x}{ext}'.format(crc=crc & 0xffffffff, ext=ext)
        result = save_uploaded_photo(file_name, fil.BINvalue, 'uploads/' + month + '/', original_file_name)
        if result.failed:
            failed.append(original_file_name)
        file_location = 'uploads/' + month + '/' + file_name
        number_uploaded += 1
        db.TblPhotos.insert(photo_path=file_location,
                            original_file_name=original_file_name,
                            uploader=auth.current_user(),
                            upload_date=datetime.datetime.now(),
                            width=result.width,
                            height=result.height,
                            crc=crc,
                            oversize=result.oversize,
                            photo_missing=False
                            )
    return dict(number_uploaded=number_uploaded, number_duplicates=number_duplicates, failed=failed)

def get_member_names(visible_only=None, gender=None):
    q = (db.TblMembers.id > 0)
    if visible_only:
        q &= (db.TblMembers.visible == True)
    if gender:
        q &= (db.TblMembers.gender == gender)

    lst = db(q).select()
    arr = [Storage(id=rec.id,
                   name=member_display_name(rec, full=True),
                   gender=rec.gender,
                   facePhotoURL=photos_folder('profile_photos') + rec.facePhotoURL if rec.facePhotoURL else 'http://' + request.env.http_host  + "/gbs/static/images/dummy_face.png") for rec in lst]
    return arr

def older_display_name(rec, full):
    s = rec.Name or ''
    if full and rec.FormerName:
        s += ' ({})'.format(rec.FormerName)
    if full and rec.NickName:
        s += ' - {}'.format(rec.NickName)
    return s

def member_display_name(rec=None, member_id=None, full=True):
    rec = rec or get_member_rec(member_id)
    if not rec:
        return ''
    if not rec.first_name:
        return older_display_name(rec, full)
    s = rec.first_name + ' ' + rec.last_name
    if full and (rec.former_first_name or rec.former_last_name):
        s += ' ('
        if rec.former_first_name:
            s += rec.former_first_name
        if rec.former_last_name:
            if rec.former_first_name:
                s += ' '
            s += rec.former_last_name
        s += ')'     
    if rec.NickName:
        s += ' - {}'.format(rec.NickName)
    return s

def get_member_rec(member_id, member_rec=None, prepend_path=False):
    if member_rec:
        rec = member_rec #used when initially all members are loaded into the cache
    elif not member_id:
        return None
    else:
        recs = db(db.TblMembers.id==member_id).select()
        rec = recs.render(0)
        rec = db(db.TblMembers.id==member_id).select().first()
    if not rec:
        return None
    rec = Storage(rec.as_dict())
    rec.full_name = member_display_name(rec, full=True)
    rec.name = member_display_name(rec, full=False)
    if prepend_path and rec.facePhotoURL:
        rec.facePhotoURL = photos_folder('profile_photos') + rec.facePhotoURL
    return rec

def get_parents(member_id):
    member_rec = get_member_rec(member_id)
    pa = member_rec.father_id
    ma = member_rec.mother_id
    pa_rec = get_member_rec(pa, prepend_path=True)
    ma_rec = get_member_rec(ma, prepend_path=True)
    parents = Storage()
    if pa_rec:
        parents.pa = pa_rec
    if ma_rec:
        parents.ma = ma_rec
    return parents

def get_siblings(member_id):
    parents = get_parents(member_id)
    if not parents:
        return []
    pa, ma = parents.pa, parents.ma
    q = db.TblMembers.id!=member_id
    if pa:
        lst1 = db(q & (db.TblMembers.father_id==pa.id)).select() if pa else []
        lst1 = [r.id for r in lst1]
    else:
        lst1 = []
    if ma:
        lst2 = db(q & (db.TblMembers.mother_id==ma.id)).select() if ma else []
        lst2 = [r.id for r in lst2]
    else:
        lst2 = []
    lst = list(set(lst1 + lst2)) #make it unique
    lst = [get_member_rec(id, prepend_path=True) for id in lst]
    return lst

def get_children(member_id):
    member_rec = get_member_rec(member_id)
    if member_rec.gender=='F' :
        q = db.TblMembers.mother_id==member_id
    elif member_rec.gender=='M':
        q = db.TblMembers.father_id==member_id
    else:
        return [] #error!
    lst = db(q).select(db.TblMembers.id)
    lst = [get_member_rec(rec.id, prepend_path=True) for rec in lst]
    return lst
    ###return [Storage(rec.as_dict()) for rec in lst] more efficient but not as safe

def get_spouses(member_id):
    children = get_children(member_id)
    member_rec = get_member_rec(member_id)
    if member_rec.gender == 'F':
        spouses = [child.father_id for child in children]
    elif member_rec.gender == 'M':
        spouses = [child.mother_id for child in children]
    else:
        spouses = [] ##error
    spouses = [sp for sp in spouses if sp]  #to handle incomplete data
    spouses = list(set(spouses))
    return [get_member_rec(m_id, prepend_path=True) for m_id in spouses]

def get_family_connections(member_info):
    result = Storage(
        parents=get_parents(member_info.id),
        siblings=get_siblings(member_info.id),
        spouses=get_spouses(member_info.id),
        children=get_children(member_info.id)
    )
    result.hasFamilyConnections = len(result.parents) > 0 or len(result.siblings) > 0 or len(result.spouses) > 0 or len(result.children) > 0
    return result

def image_url(rec):
    #for development need full http address
    return photos_folder() + rec.TblPhotos.photo_path

def get_member_slides(member_id):
    q = (db.TblMemberPhotos.Member_id==member_id) & \
        (db.TblPhotos.id==db.TblMemberPhotos.Photo_id)
    return get_slides_from_photo_list(q)

def get_portrait_candidates(member_id): #todo: not in use
    q = (db.TblMemberPhotos.Member_id==member_id) & \
        (db.TblMemberPhotos.r > 10)
    lst = db(q).select(orderby=~db.TblMemberPhotos.r)
    return lst

@serve_json
def get_faces(vars):
    photo_id = vars.photo_id;
    lst = db(db.TblMemberPhotos.Photo_id==photo_id).select()
    faces = []
    candidates = []
    for rec in lst:
        if rec.r == None: #found old record which has a member but no location
            if not rec.Member_id:
                db(db.TblMemberPhotos.id==rec.id).delete()
                continue
            name = member_display_name(member_id=rec.Member_id)
            candidate = dict(member_id=rec.Member_id, name=name)
            candidates.append(candidate)
        else:
            face = Storage(x = rec.x, y=rec.y, r=rec.r or 20, photo_id=rec.Photo_id)
            if rec.Member_id:
                face.member_id = rec.Member_id
                face.name = member_display_name(member_id=rec.Member_id)
            faces.append(face)
    return dict(faces=faces, candidates=candidates)

@serve_json
def save_face(vars):
    face = vars.face    
    assert(face.member_id > 0)
    if vars.make_profile_photo:
        face_photo_url = save_profile_photo(face)
    else:
        face_photo_url = None
    q = (db.TblMemberPhotos.Photo_id==face.photo_id) & \
        (db.TblMemberPhotos.Member_id==face.member_id)
    data = dict(
        Photo_id=face.photo_id,
        Member_id=face.member_id,
        r=face.r,
        x=face.x,
        y=face.y
    )
    rec = db(q).select().first()
    if rec:
        rec.update_record(**data)
    else:
        db.TblMemberPhotos.insert(**data) 
    member_name = member_display_name(member_id=face.member_id)
    return dict(member_name=member_name, face_photo_url=face_photo_url)
    
@serve_json
def remove_face(vars):
    face = vars.face;
    if not face.member_id:
        return dict()
    q = (db.TblMemberPhotos.Photo_id==face.photo_id) & \
        (db.TblMemberPhotos.Member_id==face.member_id)
    good = db(q).delete() == 1
    return dict(face_deleted=good)

def get_photo_list_with_topics(vars):
    first = True
    for topic in vars.selected_topics:
        q = make_query(vars) #if we do not regenerate it the query becomes accumulated and necessarily fails
        q &= (db.TblPhotoTopics.photo_id==db.TblPhotos.id)
        q &= (db.TblTopics.id==db.TblPhotoTopics.topic_id)
        q &= (db.TblTopics.id==topic.id)
        lst = db(q).select()
        lst = [rec.TblPhotos for rec in lst]
        bag1 = set(r.id for r in lst)
        if first:
            first = False
            bag = bag1
        else:
            bag &= bag1
    dic = {}
    for r in lst:
        dic[r.id] = r
    result = [dic[id] for id in bag]
    return result

def make_query(vars):
    q = (db.TblPhotos.width > 0)
    #photographer_list = [p.id for p in vars.selected_photographers]
    #if len(photographer_list) > 0:
        #q1 = (db.TblPhotos.photographer_id == photographer_list[0])
        #for p in photographer_list[1:]:
            #q1 |= dbTblPhotos.photographer_id == p
        #q &= q1         
        ### q &= db.TblPhotos.photographer_id.belongs(photographer_list) caused error
    if vars.from_date:
        from_date, acc = fix_date(vars.from_date)
        q &= (db.TblPhotos.photo_date >= from_date)
    if vars.to_date:
        to_date, acc = fix_date(vars.to_date)
        q &= (db.TblPhotos.photo_date <= to_date)
    #if vars.selected_uploader:
        #q &= db.TblPhotos.uploader==vars.uploader
    if vars.selected_days_since_upload:
        days = vars.selected_days_since_upload.value
        if days:
            upload_date = datetime.date.today() - datetime.timedelta(days=days)
            q &= (db.TblPhotos.upload_date >= upload_date)
    return q

@serve_json
def get_photo_list(vars):
    if len(vars.selected_topics) > 0:
        lst = get_photo_list_with_topics(vars)
    else:
        q = make_query(vars)
        n = db(q).count()
        if n > 1000:
            frac = 1000 * 100 / n
            sample = random.sample(range(1, 101), frac)
            ##q &= (db.TblPhotos.random_photo_key <= frac)
            q &= (db.TblPhotos.random_photo_key.belongs(sample)) #we don't want to bore our uses so there are several collections
        lst = db(q).select() ###, db.TblPhotographers.id) ##, db.TblPhotographers.id)
    if len(lst) > 1000:
        lst1 = random.sample(lst, 1000)
        lst = lst1
    result = []
    for rec in lst:
        dic = dict(
            keywords = rec.KeyWords or "",
            description = rec.Description or "",
            square_src = photos_folder('squares') + rec.photo_path,
            src=photos_folder('orig') + rec.photo_path,
            photo_id=rec.id,
            width=rec.width,
            height=rec.height
        )
        result.append(dic)
    return dict(photo_list=result)

@serve_json
def get_topic_list(vars):
    topic_list = db(db.TblTopics).select(orderby=db.TblTopics.name)
    topic_list = [dict(name=rec.name, id=rec.id) for rec in topic_list if rec.name]
    #photographer_list = db(db.TblPhotographers).select(orderby=db.TblPhotographers.name)
    #photographer_list = [dict(name=rec.name, id=rec.id) for rec in photographer_list if rec.name]
    return dict(topic_list=topic_list) ###, photographer_list=photographer_list)   

def fix_date(date_str):
    if date_str.endswith('-'):
        accuracy = 'C' #decade
        d = 1
        m = 1
        y = int(date_str[:-1])
    else:
        lst = re.split(r'[/.-]', date_str)
        lst = [int(s) for s in lst]
        if len(lst) == 3:
            if lst[2] > 1000:
                d, m, y = lst
            else:
                y, m, d = lst
            accuracy = 'D'
        elif len(lst) == 2:
            d = 1
            m, y = lst
            accuracy = 'M'
        else:
            d = 1
            m = 1
            y = int(lst[0])
            accuracy = 'Y'
    return (datetime.date(day=d, month=m, year=y), accuracy)
        
def save_profile_photo(face):
    rec = get_photo_rec(face.photo_id)
    input_path = local_photos_folder() + rec.photo_path
    facePhotoURL = "PP-{}-{}.jpg".format(face.member_id, face.photo_id)
    output_path = local_photos_folder("profile_photos") + facePhotoURL
    crop(input_path, output_path, face)
    db(db.TblMembers.id==face.member_id).update(facePhotoURL=facePhotoURL)
    return photos_folder("profile_photos") + facePhotoURL

def get_photo_rec(photo_id):
    rec = db(db.TblPhotos.id==photo_id).select().first()
    return rec

def save_story_info(story_info, used_for):
    story_text = story_info.story_text.replace('~1', '&').replace('~2', ';')
    story_id = story_info.story_id
    sm = stories_manager.Stories()
    if story_id:
        sm.update_story(story_id, story_text)
    else:
        story_id = sm.add_story(story_text, used_for=used_for)
    return story_id
