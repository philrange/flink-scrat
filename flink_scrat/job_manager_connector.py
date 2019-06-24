import os
import requests
import logging
import time

from requests.exceptions import HTTPError
from flink_scrat.exceptions import (FailedSavepointException, MaxRetriesReachedException,
                                    NotValidJARException, JobRunFailedException, JobIdNotFoundException)

logger = logging.getLogger(__name__)


class FlinkJobmanagerConnector():

    def __init__(self, address, port):
        self.path = "http://{}:{}".format(address, port)

    def handle_response(self, req_response):
        req_response.raise_for_status()
        return req_response.json()

    def list_jars(self):
        route = "{}/jars".format(self.path)
        response = self.handle_response(requests.get(route))

        return response

    def delete_jar(self, jar_id):
        route = "{}/jars/{}".format(self.path, jar_id)
        response = self.handle_response(requests.delete(route))

        return response

    def _await_savepoint_completion(self, job_id, request_id, max_retries=20, retry_sleep_seconds=2):
        in_progess_status = 'IN_PROGRESS'
        for try_num in range(0, max_retries):
            trigger_info = self.savepoint_trigger_info(job_id, request_id)
            trigger_status = trigger_info['status']['id']

            if trigger_status == in_progess_status:
                logger.debug(
                    "Savepoint still in progress. Try {}".format(try_num))

                time.sleep(retry_sleep_seconds)
                continue
            else:
                savepoint_result = trigger_info['operation']
                if('failure-cause' in savepoint_result.keys()):
                    logger.warning("Savepoint failed.")
                    raise FailedSavepointException(
                        trigger_info['operation']['failure-cause']['stack-trace'])

                else:
                    savepoint_path = savepoint_result['location']
                    logger.info("Savepoint completed path=<{}>. Job Cancelled".format(savepoint_path))
                    return savepoint_path

        logger.warning("Savepoint failed. Max retries exceded.")
        raise MaxRetriesReachedException(
            "Savepoint was not completed in time. Max retries=<{}> reached".format(max_retries))

    def cancel_job_with_savepoint(self, job_id, target_dir):
        return self.trigger_savepoint(job_id, target_dir, cancel_job=True)

    def trigger_savepoint(self, job_id, target_dir, cancel_job=False):
        logger.info("Cancelling Job=<{}> and adding savepoit to savepoint_path=<{}>".format(job_id, target_dir))
        route = "{}/jobs/{}/savepoints/".format(self.path, job_id)

        body = {
            "target-directory": target_dir,
            "cancel-job": cancel_job
        }

        try:
            response = self.handle_response(requests.post(route, json=body))

            if response is not None:
                request_id = response["request-id"]
                logger.info("Triggered savepoint for job=<{}>. Savepoint_request_id=<{}>".format(job_id, request_id))

                return self._await_savepoint_completion(job_id, request_id)
        except HTTPError as e:
            raise JobIdNotFoundException("Could not find JobId=<{}>. Reason=<{}>".format(job_id, e.response.text))

    def run_job(self, jar_id, job_params=None):
        logger.info("Starting job for deployed JAR=<{}>".format(jar_id))
        route = "{}/jars/{}/run".format(self.path, jar_id)
        try:
            response = self.handle_response(
                requests.post(route, json=job_params))
            return response
        except HTTPError as e:
            raise JobRunFailedException("Unable to start running job from jar=<{}>. Reason=<{}>"
                                        .format(jar_id, e.response.text))

    def savepoint_trigger_info(self, job_id, request_id):
        route = "{}/jobs/{}/savepoints/{}".format(
            self.path, job_id, request_id)

        return self.handle_response(requests.get(route))

    def submit_jar(self, jar_path):
        with open(jar_path, "rb") as jar:
            jar_name = os.path.basename(jar_path)
            file_dict = {'files': (jar_name, jar)}

            route = "{}/jars/upload".format(self.path)
            try:
                response = self.handle_response(
                    requests.post(route, files=file_dict))

                jar_id = os.path.basename(response['filename'])
                logger.info("Sucessfully uploaded JAR=<{}> to cluster".format(jar_id))
                return jar_id
            except HTTPError:
                logger.warning("Unable to upload JAR=<{}> to cluster".format(jar_path))
                raise NotValidJARException("File at {} is not a valid JAR".format(jar_path))

    def job_info(self, job_id):
        route = "{}/jobs/{}".format(self.path, job_id)

        return self.handle_response(requests.get(route))

    def _build_job_params(self, raw_params):
        return {key: value for key, value in raw_params.items() if value is not None}

    def submit_job(self, jar_path, target_dir=None, job_id=None, allow_non_restore=False,
                   parallelism=1, entry_class=None, extra_args=None):
        deploy_params = {
            "jar-path": jar_path,
            "target-directory": target_dir,
            "job-id": job_id
        }

        logger.info("Submiting job to cluster")
        logging.info("Deploy Parameters=<>{}".format(deploy_params))
        if job_id is not None and target_dir is not None:
            logger.info("Triggering savepoint for job=<{}>".format(job_id))
            savepoint_path = self.cancel_job_with_savepoint(job_id, target_dir)

            if savepoint_path is not None:
                job_params = self._build_job_params({
                    "allowNonRestoredState": allow_non_restore,
                    "programArg": extra_args,
                    "parallelism": parallelism,
                    "entryClass": entry_class,
                    "savepointPath": savepoint_path
                })

                logging.info("Job Parameters=<>{}".format(job_params))
                jar_id = self.submit_jar(jar_path)

                return self.run_job(jar_id, job_params)

        else:
            jar_id = self.submit_jar(jar_path)
            return self.run_job(jar_id)

    def list_jobs(self):
        route = "{}/jobs".format(self.path)
        response = self.handle_response(requests.get(route))

        return response

    def _is_job_running(self, job_id):
        running_job_status = "RUNNING"
        job_info = self.job_info(job_id)

        return job_info["state"] == running_job_status

    def _await_job_termination(self, job_id, max_retries=20, retry_sleep_seconds=2):
        for try_num in range(0, max_retries):
            if self._is_job_running(job_id):
                logger.debug(
                    "Job is running. Try {}".format(try_num))
                time.sleep(retry_sleep_seconds)
            else:
                logging.info("Job canceled sucessfully")
                return True

        logger.warning("Cancel failed. Max retries exceded.")
        raise MaxRetriesReachedException(
            "Job=<{}> could not be canceled in time. Max retries=<{}> reached".format(job_id, max_retries))

    def cancel_job(self, job_id):
        logging.info("Cancelling Job=<{}>".format(job_id))

        params = {"mode": "cancel"}
        route = "{}/jobs/{}".format(self.path, job_id)

        try:
            self.handle_response(requests.patch(route, params=params))
            return self._await_job_termination(job_id)
        except HTTPError as e:
            raise JobIdNotFoundException("Could not find job=<{}>. Reason=<{}>".format(job_id, e.response.text))

    def _find_address(self, cluster_address, cluster_port, session_name):
        apps_info = self._get_apps_info(cluster_address, cluster_port)

        running_app = self._get_running_app(apps_info, session_name)

        rpc_addres_info = self._build_rpc_address_info(running_app)

        return rpc_addres_info

    def _get_apps_info(self, cluster_address, cluster_port):
        route = "http://{}:{}/ws/v1/cluster/apps".format(cluster_address, cluster_port)
        
        result = requests.get(route)
        result.raise_for_status()

        apps_info = result.json()['apps']['app']

        return apps_info

    def _get_running_app(self, apps_info, session_name):
        running_apps = list(filter(lambda x: x['state'] == 'RUNNING' and x['name'] == session_name, apps_info))

        if len(running_apps) == 0:
            raise Exception("No app found with state=<RUNNING> and name=<{}>".format(session_name))
        elif len(running_apps) > 1:
            raise Exception("More then one app found with state=<RUNNING> and name=<{}>".format(session_name))

        running_app = running_apps[0]

        return running_app

    def _build_rpc_address_info(self, running_app):
        rpc_address, rpc_port = running_app['amRPCAddress'].split(":")

        rpc_info = {
            "rpc_address": rpc_address,
            "rpc_port": rpc_port
        }

        return rpc_info
