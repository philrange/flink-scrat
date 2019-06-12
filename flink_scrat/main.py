import argparse

from flink_scrat.job_manager_connector import FlinkJobmanagerConnector


def parse_args():
	parser = argparse.ArgumentParser(description="A python client to deploy Flink applications to a remote cluster")

	parser.add_argument("--address", dest="address", required=False, default='localhost',
		help="Address for Flink JobManager")

	parser.add_argument("--port", dest="port", required=False, default=8081,
		help="Port for Flink JobManager (default 8081)")

	cmds = parser.add_subparsers(help="sub-command help")

	submit_parser = cmds.add_parser('submit', help="Submit a job to the flink cluster")

	submit_parser.add_argument("--jar-path", dest="jar_path", required=True,
		help="Path for jar to be deployed")

	submit_parser.add_argument("--job-id", dest="job_id", required=False,
		help="Unique identifier for job to be restored")

	submit_parser.set_defaults(action="submit")

	args = parser.parse_args()
	return args


def main():
	args = parse_args()

	address = args.address
	port = args.port
	action = args.action

	conn = FlinkJobmanagerConnector(address, port)

	if action == "submit":
		conn.submit_job(args.jar_path)

if __name__ == "__main__":
	main()
