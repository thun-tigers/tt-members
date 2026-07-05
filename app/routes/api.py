from flask import Blueprint, current_app, jsonify, request

from ..extensions import db
from ..models import MemberProfile, User

bp = Blueprint('api', __name__, url_prefix='/api')


def _authorized():
    expected = current_app.config.get('INTERNAL_API_SECRET')
    provided = request.headers.get('X-TT-Internal-Secret')
    return bool(expected and provided and provided == expected)


@bp.route('/internal/messages/count', methods=['GET'])
def message_count():
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401

    auth_user_id = request.args.get('auth_user_id', type=int)
    if not auth_user_id:
        return jsonify({'error': 'auth_user_id_required'}), 400

    user = User.query.filter_by(auth_user_id=auth_user_id).first()
    if not user:
        return jsonify({'pending_messages_count': 0}), 200

    from .main import _message_items_for_user

    return jsonify({'pending_messages_count': len(_message_items_for_user(user) or [])}), 200


@bp.route('/internal/users/<int:auth_user_id>', methods=['DELETE'])
def delete_user(auth_user_id):
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401

    user = User.query.filter_by(auth_user_id=auth_user_id).first()
    if not user:
        return jsonify({'status': 'not_found'}), 404

    db.session.delete(user)
    db.session.commit()
    return jsonify({'status': 'deleted', 'auth_user_id': auth_user_id}), 200


@bp.route('/internal/users/<int:auth_user_id>', methods=['GET'])
def get_user(auth_user_id):
    if not _authorized():
        return jsonify({'error': 'unauthorized'}), 401

    user = User.query.filter_by(auth_user_id=auth_user_id).first()
    if not user:
        return jsonify({'error': 'not_found'}), 404

    profile = MemberProfile.query.filter_by(user_id=user.id).first()
    return jsonify({
        'status': 'ok',
        'user': {
            'id': user.id,
            'auth_user_id': user.auth_user_id,
            'username': user.username,
            'display_name': user.display_name,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'platform_role': user.platform_role,
            'service_role': user.service_role,
            'profile_complete': user.profile_complete,
            'position': profile.position if profile else None,
            'shirt_size': profile.shirt_size if profile else None,
        },
    })
