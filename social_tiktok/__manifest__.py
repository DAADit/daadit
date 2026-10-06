# -*- coding: utf-8 -*-
{
    'name': 'Social TikTok',
    'category': 'Marketing/Social Marketing',
    'summary': 'Manage your TikTok accounts and schedule video posts',
    'version': '19.0.1.2.0',
    'description': """Manage your TikTok accounts and schedule video posts""",
    'depends': ['social'],
    'data': [
        'data/social_media_data.xml',
        'security/ir.model.access.csv',
        'wizard/social_tiktok_credentials_wizard_views.xml',
        'views/social_tiktok_templates.xml',
        'views/social_post_template_views.xml',
        'views/social_stream_post_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'images': [
        'static/description/banner.png',
    ],
    'auto_install': False,
    'installable': True,
    'assets': {
        'web.assets_backend': [
            'social_tiktok/static/src/js/social_tiktok_add_account.js',
            'social_tiktok/static/src/js/stream_post_kanban_record.js',
            'social_tiktok/static/src/scss/social_tiktok.scss',
            'social_tiktok/static/src/xml/**/*',
        ],
    },
    'author': 'Black Monkey',
    'website': 'https://amis.lk/',
    'license': 'LGPL-3',
}
