#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
This AWS Lambda function allows to create a database and, optionally, a user
in RDS instance. Supported engines are mysql and postresql.

Endpoint, engine and master username will be obtained through AWS RDS API, you
just need to pass db instance identifier.

Master user will be granted all permissions to the created database.
If user or database already exists - they won't be created.

Author: aleksandr.fofanov@quantumsoft.ru

"""

import logging
import sys
import boto3
import os
from dataclasses import dataclass
from typing import List
import json

import psycopg2
import pymysql


@dataclass
class DBInfo:
    host: str
    port: int
    master_username: str
    master_password: str
    connect_db_name: str
    provision_db_name: str
    provision_user: str
    provision_user_password: str


class DBProvisioner(object):
    def __init__(self):
        self.logger = logging.getLogger('db-provisioner')
        self.logger.setLevel(logging.INFO)
        self.ssm_client =  boto3.client('ssm')
        self.rds_client = boto3.client('rds')

    def describe_instance(self, identifier: str) -> dict:
        response = self.rds_client.describe_db_clusters(
            DBClusterIdentifier=identifier
        )
        return response.get('DBClusters')[0]

    def get_ssm_parameter_value(self, name: str) -> str:
        response = self.ssm_client.get_parameter(
            Name=name,
            WithDecryption=True
        )
        returnval = response.get('Parameter').get('Value')
        if (name.startswith('/aws/reference/secretsmanager')):
            try:
                val = json.loads(returnval)
                returnval = val['password']
            except ValueError as e:
                pass
        return returnval

    @staticmethod
    def _get_mysql_usernames(cursor) -> List[str]:
        query = "SELECT DISTINCT user FROM mysql.user;"
        rows = []
        cursor.execute(query)
        for row in cursor:
            rows.append(row[0])
        return rows

    @staticmethod
    def _get_mysql_databases_names(cursor) -> List[str]:
        query = "SHOW DATABASES;"
        rows = []
        cursor.execute(query)
        for row in cursor:
            rows.append(row[0])
        return rows

    def provision_mysql_db(self, info: DBInfo):
        self.logger.info("Connecting to '{}' database  as user '{}'".format(info.connect_db_name, info.master_username))
        try:
            connection = pymysql.connect(
                host=info.host,
                port=info.port,
                user=info.master_username,
                password=info.master_password,
                database=info.connect_db_name,
                connect_timeout=5,
                autocommit=True
            )
        except Exception as e:
            self.logger.exception(e)
            sys.exit(1)

        self.logger.info("Successfully connected to '{}' database  as user '{}'".format(
            info.connect_db_name,
            info.master_username
        ))

        cursor = connection.cursor()
        if info.provision_user:
            usernames = self._get_mysql_usernames(cursor)
            if info.provision_user in usernames:
                self.logger.warning("User '{}' won't be created because it already exists".format(info.provision_user))
            else:
                self.logger.info("Creating user '{}'".format(info.provision_user))

                query = "CREATE USER '{}'@'localhost' IDENTIFIED BY '{}';".format(
                    info.provision_user,
                    info.provision_user_password
                )
                cursor.execute(query)
                query = "CREATE USER '{}'@'%' IDENTIFIED BY '{}';".format(
                    info.provision_user,
                    info.provision_user_password
                )
                cursor.execute(query)
                query = "GRANT ALL PRIVILEGES ON {}.* TO '{}'@'%';".format(
                    info.provision_db_name,
                    info.provision_user
                )
                cursor.execute(query)
                self.logger.info("User '{}' successfully created".format(info.provision_user))

        cursor.close()
        connection.close()

    def provision(self):

        instance = self.describe_instance(os.environ.get('DB_INSTANCE_ID'))

        master_password_ssm_param_name = os.environ.get('DB_MASTER_PASSWORD_SSM_PARAM')
        master_password = self.get_ssm_parameter_value(master_password_ssm_param_name) \
            if master_password_ssm_param_name else os.environ.get('DB_MASTER_PASSWORD')

        user_password_ssm_param_name = os.environ.get('PROVISION_USER_PASSWORD_SSM_PARAM')
        user_password = self.get_ssm_parameter_value(user_password_ssm_param_name) \
            if user_password_ssm_param_name else os.environ.get('PROVISION_USER_PASSWORD')

        db_info: DBInfo = DBInfo(
            host=instance.get('Endpoint'),
            port=instance.get('Port'),
            master_username=instance.get('MasterUsername'),
            master_password=master_password,
            connect_db_name=os.environ.get('CONNECT_DB_NAME', instance.get('DatabaseName')),
            provision_db_name=os.environ.get('PROVISION_DB_NAME'),
            provision_user=os.environ.get('PROVISION_USER'),
            provision_user_password=user_password
        )

        engine: str = instance.get('Engine')
        if engine == 'aurora-mysql':
            self.provision_mysql_db(db_info)
        else:
            raise NotImplementedError('{} engine is not supported'.format(engine))


def lambda_handler(event, context):
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    try:
        provisioner = DBProvisioner()
        provisioner.provision()
    except Exception as e:
        logger.exception(e)

    return  {'message': 'All done.'}


if __name__ == '__main__':
    lambda_handler({}, "")
