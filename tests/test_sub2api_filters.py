import unittest
from unittest.mock import Mock
from token_manager.config import default_config
from token_manager.gui_sub2api import GUISub2APIMixin
from token_manager.sub2api_policy import default_list_group_ids


class DefaultGroupFilterTest(unittest.TestCase):
    def make_app(self):
        app=GUISub2APIMixin()
        app.config=default_config()
        app.sub2api_group_filters=set()
        for name,value in [('sub2api_search_var',''),('sub2api_status_filter_var','全部状态'),('sub2api_type_filter_var','oauth')]:
            setattr(app,name,Mock(get=Mock(return_value=value)))
        app.reset_sub2api_default_groups()
        return app

    def test_defaults_to_id_two_even_if_group_name_changes(self):
        app=self.make_app()
        records=[{'id':1,'group_ids':[2],'group_names':['Renamed'],'type':'oauth'},
                 {'id':2,'group_ids':[24],'group_names':['Renamed'],'type':'oauth'},
                 {'id':3,'group_ids':[2],'type':'apikey'}]
        self.assertEqual([r['id'] for r in app.filter_sub2api_records(records)],[1])
        app.config['integrations']['sub2api']['default_list_group_ids']='24'
        app.reset_sub2api_default_groups()
        self.assertEqual([r['id'] for r in app.filter_sub2api_records(records)],[2])
        self.assertEqual(app.config['integrations']['sub2api']['group_ids'],'2')

    def test_blank_shows_all_and_reset_restores_configured_ids(self):
        app=self.make_app()
        app.config['integrations']['sub2api']['default_list_group_ids']=''
        app.reset_sub2api_default_groups()
        self.assertFalse(app.sub2api_group_filter_ids)
        app.config['integrations']['sub2api']['default_list_group_ids']='2,24'
        app.reset_sub2api_default_groups()
        self.assertEqual(app.sub2api_group_filter_ids,{2,24})

    def test_invalid_ids_rejected(self):
        self.assertEqual(default_list_group_ids(' 24，2,2 '),{2,24})
        for value in ('-1','0','2,abc','2.5'):
            with self.assertRaises(ValueError):default_list_group_ids(value)
