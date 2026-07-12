{
    "problem_data": {
        "problem_name": "{{problem_name}}",
        "parallel_type": "OpenMP",
        "echo_level": 1,
        "start_time": 0.0,
        "end_time": 1.0
    },
    "solver_settings": {
        "solver_type": "eigen_value",
        "scheme_type": "dynamic",
        "model_part_name": "Structure",
        "domain_size": "{{domain_size}}",
        "echo_level": 1,
        "model_import_settings": {
            "input_type": "mdpa",
            "input_filename": "{{mdpa_basename}}"
        },
        "material_import_settings": {
            "materials_filename": "{{materials_filename}}"
        },
        "time_stepping": {
            "time_step": 1.1
        },
        "eigensolver_settings": {
            "solver_type": "eigen_eigensystem",
            "number_of_eigenvalues": "{{num_eigenvalues}}",
            "max_iteration": 1000,
            "tolerance": 1e-6,
            "echo_level": 1
        },
        "rotation_dofs": false
    },
    "processes": {
        "constraints_process_list": [{
            "python_module": "assign_vector_variable_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "AssignVectorVariableProcess",
            "Parameters": {
                "model_part_name": "{{fix_model_part}}",
                "variable_name": "DISPLACEMENT",
                "interval": [0.0, "End"],
                "constrained": [true, true, true],
                "value": [0.0, 0.0, 0.0]
            }
        }],
        "loads_process_list": [],
        "list_other_processes": []
    },
    "output_processes": {
        "gid_output": [],
        "vtk_output": []
    },
    "analysis_stage": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis"
}
