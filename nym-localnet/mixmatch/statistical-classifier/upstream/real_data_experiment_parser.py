import os
import numpy as np
import json

# experiment_database = 'baseline'
# experiment_database = 'no-cover'
# experiment_database = 'live-nym'

def parse_real_data(databases_path = '../datasets', experiment_database = 'baseline'):
  experiment_database_path = databases_path + '/' + experiment_database
  experiment_folder_contents = os.listdir(experiment_database_path)
  folders_list = []
  print('Number of entries in database folder: %i' % len(experiment_folder_contents))
  for content in experiment_folder_contents:
    if len(content) > 80:
      if os.path.isdir(experiment_database_path + '/' + content):
        folders_list.append(content)
  print('Number of flow folders in database folder: %i' % len(folders_list))
  json_data_list = []
  # start_times = np.loadtxt(experiment_database_path + '/start_times.txt', dtype=np.dtype(np.int64))
  # min_start_times = np.min(start_times)
  for folder in folders_list:
    print('\rProcessing folder %s ...' % folder)
    with open(experiment_database_path + '/' + folder + '/flowpair.json', 'r') as f:
      json_data = json.load(f)
      start_time = json_data['start']
      responder_to_gateway = json_data['responder']['to_gateway']
      responder_from_gateway = json_data['responder']['from_gateway']
      initiator_to_gateway = json_data['initiator']['to_gateway']
      initiator_from_gateway = json_data['initiator']['from_gateway']
      responder_to_gateway_np = np.array(responder_to_gateway, dtype=np.dtype('int64'))
      responder_from_gateway_np = np.array(responder_from_gateway, dtype=np.dtype('int64'))
      initiator_to_gateway_np = np.array(initiator_to_gateway, dtype=np.dtype('int64'))
      initiator_from_gateway_np = np.array(initiator_from_gateway, dtype=np.dtype('int64'))
      responder_to_gateway_np[:,0] -= start_time
      responder_from_gateway_np[:,0] -= start_time
      initiator_to_gateway_np[:,0] -= start_time
      initiator_from_gateway_np[:,0] -= start_time
      # Store data concerning responder_to_gateway
    payload_responder_to_gateway = responder_to_gateway_np[responder_to_gateway_np[:,1] == 2413,0]
    ack_responder_to_gateway = responder_to_gateway_np[responder_to_gateway_np[:,1] != 2413,0]
    payload_responder_to_gateway_file = open(experiment_database_path + '/' + folder + '/payload_responder_to_gateway.txt', 'w')
    for index in range(payload_responder_to_gateway.shape[0]):
      print(payload_responder_to_gateway[index], file=payload_responder_to_gateway_file)
    payload_responder_to_gateway_file.close()
    assert(ack_responder_to_gateway.shape[0] == 0)
    # Store data concerning responder_from_gateway
    payload_responder_from_gateway = responder_from_gateway_np[responder_from_gateway_np[:,1] == 1675,0]
    ack_responder_from_gateway = responder_from_gateway_np[responder_from_gateway_np[:,1] != 1675,0]
    payload_responder_from_gateway_file = open(experiment_database_path + '/' + folder + '/payload_responder_from_gateway.txt', 'w')
    for index in range(payload_responder_from_gateway.shape[0]):
      print(payload_responder_from_gateway[index], file=payload_responder_from_gateway_file)
    payload_responder_from_gateway_file.close()
    ack_responder_from_gateway_file = open(experiment_database_path + '/' + folder + '/ack_responder_from_gateway.txt', 'w')
    for index in range(ack_responder_from_gateway.shape[0]):
      print(ack_responder_from_gateway[index], file=ack_responder_from_gateway_file)
    ack_responder_from_gateway_file.close()
    # Store data concerning initiator_to_gateway
    payload_initiator_to_gateway = initiator_to_gateway_np[initiator_to_gateway_np[:,1] == 2413,0]
    ack_initiator_to_gateway = initiator_to_gateway_np[initiator_to_gateway_np[:,1] != 2413,0]
    payload_initiator_to_gateway_file = open(experiment_database_path + '/' + folder + '/payload_initiator_to_gateway.txt', 'w')
    for index in range(payload_initiator_to_gateway.shape[0]):
      print(payload_initiator_to_gateway[index], file=payload_initiator_to_gateway_file)
    payload_initiator_to_gateway_file.close()
    assert(ack_initiator_to_gateway.shape[0] == 0)
    # Store data concerning initiator_from_gateway
    payload_initiator_from_gateway = initiator_from_gateway_np[initiator_from_gateway_np[:,1] == 1675,0]
    ack_initiator_from_gateway = initiator_from_gateway_np[initiator_from_gateway_np[:,1] != 1675,0]
    payload_initiator_from_gateway_file = open(experiment_database_path + '/' + folder + '/payload_initiator_from_gateway.txt', 'w')
    for index in range(payload_initiator_from_gateway.shape[0]):
      print(payload_initiator_from_gateway[index], file=payload_initiator_from_gateway_file)
    payload_initiator_from_gateway_file.close()
    ack_initiator_from_gateway_file = open(experiment_database_path + '/' + folder + '/ack_initiator_from_gateway.txt', 'w')
    for index in range(ack_initiator_from_gateway.shape[0]):
      print(ack_initiator_from_gateway[index], file=ack_initiator_from_gateway_file)
    ack_initiator_from_gateway_file.close()


if __name__ == '__main__':
  parse_real_data()
