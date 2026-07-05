from pathlib import Path


def test_license_photo_route_allows_owner_access(client, app, tmp_path):
    from app.extensions import db
    from app.models import MemberProfile, User

    upload_root = tmp_path / 'uploads'
    photo_dir = upload_root / 'license-photos'
    photo_dir.mkdir(parents=True)
    photo_file = photo_dir / 'license_1_test.jpg'
    photo_file.write_bytes(b'fake-image-data')

    with app.app_context():
        app.config['UPLOAD_ROOT'] = str(upload_root)
        user = User(
            auth_user_id=42,
            username='player-user',
            first_name='Player',
            last_name='One',
            display_name='Player One',
            email='player@example.com',
            platform_role='user',
            service_role='user',
            profile_complete=True,
        )
        db.session.add(user)
        db.session.flush()
        owner_user_id = user.id
        db.session.add(MemberProfile(user_id=user.id, first_name='Player', last_name='One', license_photo_filename=photo_file.name))
        db.session.commit()

    with client.session_transaction() as session:
        session['user_id'] = owner_user_id

    response = client.get(f'/members/{owner_user_id}/license-photo')

    assert response.status_code == 200
    assert response.data == b'fake-image-data'


def test_license_photo_route_blocks_other_users(client, app, tmp_path):
    from app.extensions import db
    from app.models import MemberProfile, User

    upload_root = tmp_path / 'uploads'
    photo_dir = upload_root / 'license-photos'
    photo_dir.mkdir(parents=True)
    photo_file = photo_dir / 'license_1_test.jpg'
    photo_file.write_bytes(b'fake-image-data')

    with app.app_context():
        app.config['UPLOAD_ROOT'] = str(upload_root)
        owner = User(
            auth_user_id=42,
            username='player-user',
            first_name='Player',
            last_name='One',
            display_name='Player One',
            email='player@example.com',
            platform_role='user',
            service_role='user',
            profile_complete=True,
        )
        other = User(
            auth_user_id=43,
            username='other-user',
            first_name='Other',
            last_name='User',
            display_name='Other User',
            email='other@example.com',
            platform_role='user',
            service_role='user',
            profile_complete=True,
        )
        db.session.add(owner)
        db.session.add(other)
        db.session.flush()
        owner_user_id = owner.id
        other_user_id = other.id
        db.session.add(MemberProfile(user_id=owner_user_id, first_name='Player', last_name='One', license_photo_filename=photo_file.name))
        db.session.commit()

    with client.session_transaction() as session:
        session['user_id'] = other_user_id

    response = client.get(f'/members/{owner_user_id}/license-photo')

    assert response.status_code == 403