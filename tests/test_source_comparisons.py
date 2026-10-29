"""Proposals bind exact source versions and publish only with completed work."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import agents, agent_artifacts, notebooks, pages, source_search, source_comparisons, storage, notebook_work


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.tmp.name},clear=True)
        self.env.start();self.addCleanup(self.tmp.cleanup);self.addCleanup(self.env.stop)
        self.book=notebooks.create('Review')['id'];self.agent=agents.assistant()
        self.first=pages.create('Option A',self.book,'The first option costs 100 units and takes two weeks.')
        self.second=pages.create('Option B',self.book,'The second option costs 120 units and takes three weeks.')
        self.sources=[{'source_id':d[1],'title':d[3],'excerpt':d[4],'target':d[5]} for d in source_search._documents() if d[2]==self.book]
        self.value={'question':'Compare costs and timing?', 'sources':[{'source_index':i+1,'quote':s['excerpt'].split('\n')[-1]} for i,s in enumerate(self.sources)]}

    def proposal(self):
        candidate=source_comparisons.validate(self.value,self.sources)
        job=agents.start_job(self.agent['id'],self.book)
        message=agents.append_message(self.agent['id'],'agent','Source review',self.book,job)
        artifact=agent_artifacts.create(self.agent['id'],message['id'],self.book,'Source review','Review',[],comparison=candidate)
        return job,artifact

    def test_publish_dedupe_and_dismiss(self):
        job,_=self.proposal();self.assertEqual(source_comparisons.list_for(self.book),[])
        agents.finish_job(job,'succeeded')
        item=source_comparisons.list_for(self.book)[0]
        second,_=self.proposal();agents.finish_job(second,'succeeded')
        self.assertEqual(len(source_comparisons.list_for(self.book)),1)
        self.assertEqual(len(source_comparisons.current_sources(item)),2)
        source_comparisons.dismiss(self.book,item['id'])
        with self.assertRaises(ValueError):asyncio.run(source_comparisons.compare(self.book,item['id']))

    def test_invalid_quotes_duplicates_and_changed_sources(self):
        self.value['sources'][1]['source_index']=1
        with self.assertRaises(ValueError):source_comparisons.validate(self.value,self.sources)
        self.value['sources'][1]['source_index']=2
        self.value['sources'][0]['quote']='This quote does not exist in the source.'
        with self.assertRaises(ValueError):source_comparisons.validate(self.value,self.sources)
        self.value['sources'][0]['quote']=self.sources[0]['excerpt'].split('\n')[-1]
        job,_=self.proposal();agents.finish_job(job,'succeeded');item=source_comparisons.list_for(self.book)[0]
        with storage.connect() as conn:conn.execute('UPDATE pages SET markdown=? WHERE id=?',('Updated content',self.first['id']))
        with self.assertRaises(ValueError):source_comparisons.current_sources(item)
        with self.assertRaises(ValueError):asyncio.run(source_comparisons.compare(self.book,item['id']))

    def test_moved_source_cannot_run(self):
        job,_=self.proposal();agents.finish_job(job,'succeeded');item=source_comparisons.list_for(self.book)[0]
        pages.assign_notebook(self.first['id'],None)
        with self.assertRaises(ValueError):source_comparisons.current_sources(item)

    def test_interrupted_comparison_can_be_dismissed_without_rerunning(self):
        job,_=self.proposal();agents.finish_job(job,'succeeded')
        item=source_comparisons.list_for(self.book)[0]
        child=agents.start_job(self.agent['id'],self.book)
        with storage.connect() as conn:conn.execute("UPDATE agent_comparisons SET state='running',job_id=? WHERE id=?",(child,item['id']))
        activity=notebook_work.activity(self.book)
        self.assertEqual(activity['counts']['working'],1)
        self.assertFalse(any(row['kind']=='job' and row['id']==child for row in activity['items']))
        agents.finish_job(child,'interrupted','Stopped')
        self.assertEqual(source_comparisons.get(self.book,item['id'])['state'],'retry')
        self.assertEqual(source_comparisons.dismiss(self.book,item['id'])['state'],'dismissed')
