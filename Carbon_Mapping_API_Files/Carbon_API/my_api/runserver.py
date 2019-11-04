# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# # /ai4e_api_tools has been added to the PYTHONPATH, so we can reference those
# libraries directly.
from time import sleep
import json
from flask import Flask, request, abort
from ai4e_app_insights_wrapper import AI4EAppInsights
from ai4e_service import APIService
from os import getenv
import sys
import glob
import zipfile
import os
from rasterstats import zonal_stats
import fiona
from geojson import Point, Feature, FeatureCollection, dump
import geopandas as gpd
import requests

print('Creating Application')
print('Starting downloading Geotiff')
url = 'https://gfw-files.s3.amazonaws.com/ai4e/Predicted_Sequestration_Rate_Map.tif'
r = requests.get(url, stream=True)
chunk_size = 20000000
with open(os.path.join(os.getcwd(),'Predicted_Sequestration_Rate_Map.tif'), 'wb') as fd:
    for chunk in r.iter_content(chunk_size):
        fd.write(chunk)
print('Finished downloading Geotiff')
app = Flask(__name__)

# Use the AI4EAppInsights library to send log messages. NOT REQURIED
log = AI4EAppInsights()

# Use the APIService to executes your functions within a logging trace, supports long-running/async functions,
# handles SIGTERM signals from AKS, etc., and handles concurrent requests.
with app.app_context():
    ai4e_service = APIService(app, log)

# Define a function for processing request data, if appliciable.  This function loads data or files into
# a dictionary for access in your API function.  We pass this function as a parameter to your API setup.
def process_request_data(request):
    print(request)
    return_values = {'data': None}
    try:
        # Attempt to load the body
        return_values['data'] = json.loads(request.data)#BytesIO(request.data) #json.loads(request.data)#
    except:
        log.log_error(request)
        log.log_error('Unable to load the request data')   # Log to Application Insights
    return return_values


# Define a function that runs your model.  This could be in a library.
def run_model(taskId, body):
    # Update the task status, so the caller knows it has been accepted and is running.
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'running model')
    log.log_debug('Running model', taskId) # Log to Application Insights
    
    features = body.get('features')
    features = FeatureCollection(features)
    
    temp_geojson = os.path.join(os.getcwd(),'temp.geojson')
    temp_shapefile =  os.path.join(os.getcwd(),'temp.shp')
    
    log.log_debug('Writing to geojson', taskId)
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'Writing to geojson')
    
    with open(temp_geojson, 'w') as f:
        dump(features, f)
        
    log.log_debug('Converting to shapefile', taskId)
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'Converting to shapefile')
    
    df = gpd.read_file(temp_geojson)
    df.to_file(temp_shapefile)
    
    raster_file = os.path.join(os.getcwd(),'Predicted_Sequestration_Rate_Map.tif')
    
    log.log_debug('Running Zonal Stats', taskId)
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'Running Zonal Stats')
    
    stats = zonal_stats(temp_shapefile, raster_file)
    log.log_debug('Completed: Here are the statistics for your region(s): '.format(stats), taskId) # Log to Application Insights
    ai4e_service.api_task_manager.CompleteTask(taskId, 'Completed: Here are the statistics for your region(s): {}'.format(stats))
    return stats
    
# POST, long-running/async API endpoint example
@ai4e_service.api_async_func(
    api_path = '/example', 
    methods = ['POST'], 
    request_processing_function = process_request_data, # This is the data process function that you created above.
    maximum_concurrent_requests = 5, # If the number of requests exceed this limit, a 503 is returned to the caller.
    content_types = ['application/json'],
    content_max_length = 100000000, # In bytes
    trace_name = 'post:my_long_running_funct')
    
def default_post(*args, **kwargs):
    try:
        # Since this is an async function, we need to keep the task updated.
        taskId = kwargs.get('taskId')
        log.log_debug('Started task', taskId) # Log to Application Insights

        # Get the data from the dictionary key that you assigned in your process_request_data function.
        request_data = kwargs.get('data')
    
        if not request_data:
            ai4e_service.api_task_manager.FailTask(taskId, 'Task failed - Body was empty or could not be parsed.')
            return -1

        # Run your model function
        ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'Start Run Model')
        results = run_model(taskId, request_data)

    except:
        print('Default post failed')
        print(sys.exc_info()[0])

# GET, sync API endpoint example
@ai4e_service.api_sync_func(api_path = '/echo/<string:text>', methods = ['GET'], maximum_concurrent_requests = 1000, trace_name = 'get:echo', kwargs = {'text'})
def echo(*args, **kwargs):
    return 'Echo: ' + kwargs['text']

if __name__ == '__main__':
    app.run()